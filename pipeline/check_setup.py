"""Quick test of every key and service. Run from the Actions tab ("Check setup") or locally:
python -m pipeline.check_setup"""
import requests

from . import config, telegram_bot


def check(name, fn):
    try:
        print(f"OK   {name}: {fn()}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"FAIL {name}: {e}")
        return False


def _llm(base_url, key, model):
    from openai import OpenAI
    r = OpenAI(base_url=base_url, api_key=key, timeout=60).chat.completions.create(
        model=model, messages=[{"role": "user", "content": "Reply with the word OK"}], max_tokens=5)
    return f"{model} -> {r.choices[0].message.content.strip()}"


def main():
    results = [
        check("Gemini", lambda: _llm("https://generativelanguage.googleapis.com/v1beta/openai/",
                                     config.GEMINI_API_KEY, config.GEMINI_MODEL)),
        check("Groq", lambda: _llm("https://api.groq.com/openai/v1", config.GROQ_API_KEY, config.GROQ_MODEL)),
        check("OpenRouter", lambda: _llm("https://openrouter.ai/api/v1", config.OPENROUTER_API_KEY,
                                         config.OPENROUTER_MODEL)),
        check("Pixabay", lambda: f"{requests.get('https://pixabay.com/api/videos/', params={'key': config.PIXABAY_API_KEY, 'q': 'technology'}, timeout=30).json()['totalHits']} clips for 'technology'"),
        check("Telegram", lambda: telegram_bot.send_message("✅ AI News Shorts setup check: Telegram works.")["message_id"]),
        check("YouTube", lambda: f"channel = {__import__('pipeline.youtube', fromlist=['x']).channel_title()}"),
    ]
    print(f"\n{sum(results)}/{len(results)} checks passed. At least one LLM must pass; all others are required.")


if __name__ == "__main__":
    main()
