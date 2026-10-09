"""Quick test of every key and service. Run from the Actions tab ("Check setup") or locally:
python -m pipeline.check_setup"""
import requests

from . import config, facebook, instagram, telegram_bot


def check(name, fn):
    try:
        print(f"OK   {name}: {fn()}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"FAIL {name}: {e}")
        return False


def _llm(provider):
    from .llm import chat_json
    data, used = chat_json("Reply with JSON only.", 'Return {"ok": true}', temperature=0, only=provider, rounds=1)
    return f"{used} -> {data}"


def main():
    results = [
        check("Gemini", lambda: _llm("gemini")),
        check("Groq", lambda: _llm("groq")),
        check("OpenRouter", lambda: _llm("openrouter")),
        check("Pixabay", lambda: f"{requests.get('https://pixabay.com/api/videos/', params={'key': config.PIXABAY_API_KEY, 'q': 'technology'}, timeout=30).json()['totalHits']} clips for 'technology'"),
        check("Telegram", lambda: telegram_bot.send_message("✅ AI News Shorts setup check: Telegram works.")["message_id"]),
        check("YouTube", lambda: f"channel = {__import__('pipeline.youtube', fromlist=['x']).channel_title()}"),
    ]
    if instagram.enabled():
        results.append(check("Instagram", lambda: f"account = @{instagram.username()}"))
    else:
        print("SKIP Instagram: IG_USER_ID / IG_ACCESS_TOKEN not set")
    if facebook.enabled():
        results.append(check("Facebook", lambda: f"page = {facebook.page_name()}"))
    else:
        print("SKIP Facebook: FB_PAGE_ID not set")
    print(f"\n{sum(results)}/{len(results)} checks passed. At least one LLM must pass; all others are required (Instagram/Facebook optional).")


if __name__ == "__main__":
    main()
