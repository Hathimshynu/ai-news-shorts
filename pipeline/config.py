"""Central settings. Every value can be overridden with an environment variable
(GitHub Secrets for keys, GitHub Variables or the workflow `env:` block for the rest)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"                       # final.mp4, thumb.jpg, meta.json (uploaded as artifact)
JOB = ROOT / "renderer" / "public" / "job"  # assets Remotion reads via staticFile()
DATA = ROOT / "data"
TEMPLATES = ROOT / "templates"


def env(name, default=None, required=False):
    val = os.getenv(name) or default  # empty string (unset GitHub variable) falls back to default
    if required and not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


# ---- API keys (GitHub Secrets) ----
GEMINI_API_KEY = env("GEMINI_API_KEY")
GROQ_API_KEY = env("GROQ_API_KEY")
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY")
PIXABAY_API_KEY = env("PIXABAY_API_KEY")
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID")
YT_CLIENT_ID = env("YT_CLIENT_ID")
YT_CLIENT_SECRET = env("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = env("YT_REFRESH_TOKEN")
IG_USER_ID = env("IG_USER_ID")            # optional: Instagram publishing
IG_ACCESS_TOKEN = env("IG_ACCESS_TOKEN")
FB_PAGE_ID = env("FB_PAGE_ID")              # optional: Facebook Page Reels
FB_PAGE_TOKEN = env("FB_PAGE_TOKEN")        # optional; defaults to IG_ACCESS_TOKEN

# ---- Models (change here when a provider renames a model) ----
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-3.8-flash")
GROQ_MODEL = env("GROQ_MODEL", "llama-3.3-70b-versatile")
OPENROUTER_MODEL = env("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")

# ---- Content settings ----
CHANNEL_NAME = env("CHANNEL_NAME", "AI News Shorts")
CHANNEL_HANDLE = env("CHANNEL_HANDLE", "")
if CHANNEL_HANDLE.strip().lower() in ("none", "-", "off"):
    CHANNEL_HANDLE = ""  # no name on videos/thumbnails
# Google News English editions for India + Asia. Format: country code used in hl/gl/ceid.
COUNTRIES = [c.strip() for c in env("NEWS_COUNTRIES", "IN,SG,MY,PH,PK").split(",") if c.strip()]
SEARCH_QUERIES = [q.strip() for q in env(
    "NEWS_QUERIES",
    "artificial intelligence,AI India,smartphone launch India,ISRO,startup funding India,"
    "cybersecurity,OpenAI,Google AI,Nvidia,UPI",
).split(",") if q.strip()]
INDIA_KEYWORDS = ["india", "indian", "bengaluru", "bangalore", "mumbai", "delhi", "hyderabad",
                  "chennai", "kerala", "isro", "upi", "jio", "airtel", "tata", "reliance",
                  "infosys", "wipro", "rupee", "₹", "meity", "nasscom"]
REPEAT_WINDOW_DAYS = int(env("REPEAT_WINDOW_DAYS", "7"))

# ---- Voice / captions ----
TTS_VOICE = env("TTS_VOICE", "en-IN-PrabhatNeural")  # or en-IN-NeerjaNeural
TTS_RATE = env("TTS_RATE", "+8%")
MAX_AUDIO_SECONDS = float(env("MAX_AUDIO_SECONDS", "57"))
WHISPER_MODEL = env("WHISPER_MODEL", "base")  # base = fast on CI; "small" = more accurate

# ---- YouTube ----
YT_PRIVACY = env("YT_PRIVACY", "private")  # switch to "public" after the API audit is approved
YT_CATEGORY_ID = "28"  # Science & Technology
AFFILIATE_FOOTER = env("AFFILIATE_FOOTER", "")  # e.g. ebook / affiliate links appended to every description
