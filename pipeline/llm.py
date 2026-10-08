"""One chat() function over three free providers. If one fails or hits its rate limit,
the next is tried automatically: Gemini -> Groq -> OpenRouter."""
import json
import re

from openai import OpenAI

from . import config


def _providers():
    p = []
    if config.GEMINI_API_KEY:
        p.append(("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/",
                  config.GEMINI_API_KEY, config.GEMINI_MODEL))
    if config.GROQ_API_KEY:
        p.append(("groq", "https://api.groq.com/openai/v1", config.GROQ_API_KEY, config.GROQ_MODEL))
    if config.OPENROUTER_API_KEY:
        p.append(("openrouter", "https://openrouter.ai/api/v1", config.OPENROUTER_API_KEY, config.OPENROUTER_MODEL))
    if not p:
        raise RuntimeError("No LLM API keys configured")
    return p


def _extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model output")
    return json.loads(text[start:end + 1])


def chat_json(system, user, temperature=0.7):
    """Return (parsed_json, provider_name)."""
    errors = []
    for name, base_url, key, model in _providers():
        try:
            client = OpenAI(base_url=base_url, api_key=key, timeout=90, max_retries=1)
            kwargs = dict(model=model, temperature=temperature,
                          messages=[{"role": "system", "content": system},
                                    {"role": "user", "content": user}])
            try:
                resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
            except Exception:  # some free models reject response_format; retry without it
                resp = client.chat.completions.create(**kwargs)
            data = _extract_json(resp.choices[0].message.content)
            print(f"[llm] answered by {name} ({model})")
            return data, name
        except Exception as e:  # noqa: BLE001
            print(f"[llm] {name} failed: {e}")
            errors.append(f"{name}: {e}")
    raise RuntimeError("All LLM providers failed:\n" + "\n".join(errors))
