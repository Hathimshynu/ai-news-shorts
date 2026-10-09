"""LLM router over three free providers with automatic model discovery and fallback.

For every request it tries, in order:  Gemini -> Groq -> OpenRouter, and inside each provider
several free models (discovered live from the provider, so renamed/retired models don't break
the pipeline). Rules:
  - 429 / 5xx / timeout / bad JSON  -> try the next model, then the next provider
  - 404 / 400 (model gone)          -> skip that model for the rest of the run
  - 401 / 403 (bad API key)         -> skip that provider for the rest of the run
If everything fails, it waits (30s, then 60s) and tries all of them again before giving up.
"""
import json
import re
import time

import requests
from openai import OpenAI

from . import config

ROUNDS = 3
WAITS = [30, 60]
MAX_MODELS_PER_PROVIDER = 4

_models_cache = {}
_dead_providers = {}   # name -> reason (bad key)
_bad_models = set()    # (provider, model)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GROQ_URL = "https://api.groq.com/openai/v1"
OPENROUTER_URL = "https://openrouter.ai/api/v1"

GROQ_PREFERRED = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "moonshotai/kimi-k2-instruct",
                  "qwen/qwen3-32b", "openai/gpt-oss-20b", "llama-3.1-8b-instant"]
OPENROUTER_HINTS = ["llama-3.3-70b", "gpt-oss", "qwen", "deepseek", "gemma", "mistral", "llama"]


def _uniq(seq):
    seen = set()
    return [x for x in seq if x and not (x in seen or seen.add(x))]


def _version(name):
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else 0.0


def _discover_gemini(key):
    found = []
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         params={"key": key, "pageSize": 200}, timeout=20)
        if r.status_code in (401, 403):
            _dead_providers["gemini"] = f"API key rejected ({r.status_code})"
        for m in r.json().get("models", []):
            name = m.get("name", "").split("/", 1)[-1]
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            if "flash" not in name or re.search(r"image|tts|audio|live|embed|vision", name):
                continue
            found.append(name)
    except Exception as e:  # noqa: BLE001
        print(f"[llm] gemini model list failed: {e}")
    # newest stable flash first, then lite, then preview/experimental
    found.sort(key=lambda n: (("preview" in n or "exp" in n), "lite" in n, -_version(n)))
    return _uniq([config.GEMINI_MODEL] + found)


def _discover_groq(key):
    found = []
    try:
        r = requests.get(f"{GROQ_URL}/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        if r.status_code in (401, 403):
            _dead_providers["groq"] = f"API key rejected ({r.status_code}). Groq keys start with 'gsk_'"
        ids = [m["id"] for m in r.json().get("data", [])]
        ids = [i for i in ids if not re.search(r"whisper|tts|guard|embed|playai|orpheus|distil", i)]
        found = [m for m in GROQ_PREFERRED if m in ids] + [i for i in ids if i not in GROQ_PREFERRED]
    except Exception as e:  # noqa: BLE001
        print(f"[llm] groq model list failed: {e}")
        found = GROQ_PREFERRED
    return _uniq([config.GROQ_MODEL] + found)


def _discover_openrouter(key):
    found = []
    try:
        data = requests.get(f"{OPENROUTER_URL}/models", timeout=20).json().get("data", [])
        free = []
        for m in data:
            p = m.get("pricing") or {}
            try:
                is_free = float(p.get("prompt", 1)) == 0 and float(p.get("completion", 1)) == 0
            except (TypeError, ValueError):
                is_free = False
            out_mod = (m.get("architecture") or {}).get("output_modalities") or ["text"]
            if is_free and "text" in out_mod and (m.get("context_length") or 0) >= 16000:
                free.append(m)

        def rank(m):
            mid = m["id"].lower()
            hint = next((i for i, h in enumerate(OPENROUTER_HINTS) if h in mid), len(OPENROUTER_HINTS))
            return (hint, -(m.get("context_length") or 0))

        found = [m["id"] for m in sorted(free, key=rank)]
    except Exception as e:  # noqa: BLE001
        print(f"[llm] openrouter model list failed: {e}")
    return _uniq([config.OPENROUTER_MODEL] + found)


def _providers():
    plan = []
    for name, url, key, discover in (
        ("gemini", GEMINI_URL, config.GEMINI_API_KEY, _discover_gemini),
        ("groq", GROQ_URL, config.GROQ_API_KEY, _discover_groq),
        ("openrouter", OPENROUTER_URL, config.OPENROUTER_API_KEY, _discover_openrouter),
    ):
        if not key:
            continue
        if name not in _models_cache:
            _models_cache[name] = discover(key)
            print(f"[llm] {name} candidates: {_models_cache[name][:MAX_MODELS_PER_PROVIDER]}")
        plan.append((name, url, key, _models_cache[name]))
    if not plan:
        raise RuntimeError("No LLM API keys configured")
    return plan


def _extract_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model output")
    return json.loads(text[start:end + 1])


def _status(e):
    return getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)


def _ask(client, model, system, user, temperature):
    kwargs = dict(model=model, temperature=temperature,
                  messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    try:
        resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
    except Exception as e:  # noqa: BLE001
        if _status(e) != 400:
            raise
        resp = client.chat.completions.create(**kwargs)  # model doesn't support JSON mode
    return _extract_json(resp.choices[0].message.content)


def chat_json(system, user, temperature=0.7, only=None, rounds=ROUNDS):
    """Return (parsed_json, "provider:model")."""
    errors = []
    for rnd in range(rounds):
        for name, url, key, models in _providers():
            if (only and name != only) or name in _dead_providers:
                continue
            client = OpenAI(base_url=url, api_key=key, timeout=90, max_retries=0)
            tried = 0
            for model in models:
                if tried >= MAX_MODELS_PER_PROVIDER:
                    break
                if (name, model) in _bad_models:
                    continue
                tried += 1
                try:
                    data = _ask(client, model, system, user, temperature)
                    print(f"[llm] answered by {name}:{model}")
                    return data, f"{name}:{model}"
                except Exception as e:  # noqa: BLE001
                    code = _status(e)
                    msg = f"{name}:{model} -> {code or type(e).__name__}: {str(e)[:160]}"
                    print(f"[llm] {msg}")
                    errors.append(msg)
                    if code in (401, 403):
                        _dead_providers[name] = f"API key rejected ({code})"
                        break
                    if code in (400, 404):
                        _bad_models.add((name, model))
                    # 429 / 5xx / timeout / bad JSON: just move on to the next model
        if rnd < rounds - 1:
            wait = WAITS[min(rnd, len(WAITS) - 1)]
            print(f"[llm] all providers busy/failed, retrying in {wait}s (round {rnd + 2}/{rounds})")
            time.sleep(wait)
    dead = "".join(f"\n{n}: {r}" for n, r in _dead_providers.items())
    raise RuntimeError("All LLM providers failed." + (f"\nFix these keys:{dead}" if dead else "")
                       + "\nLast errors:\n" + "\n".join(errors[-6:]))
