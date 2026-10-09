"""Trend discovery: collect candidate stories from many free sources, merge duplicates, score them.

Sources (every one is best-effort: if a site is down or blocks us, the others still work):
  news       Google News RSS for India + Asia editions and India-focused tech searches
  official   AI-lab and platform blogs (OpenAI, Google, DeepMind, Microsoft, NVIDIA, Hugging Face, GitHub)
  community  Hacker News front page, Reddit AI/tech subreddits
  github     Fast-rising new AI repositories (GitHub search API)
  launch     Product Hunt launches
  search     Google Trends daily searches for India (tech-related only)
Runner-up topics are kept in data/backlog.json for up to 2 days, so a quiet news day still has material.
"""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlparse

import feedparser
import requests
from rapidfuzz import fuzz

from . import config

UA = {"User-Agent": "Mozilla/5.0 (compatible; tech-talk-hathim-bot/1.0)"}
USED_FILE = config.DATA / "used_topics.json"
BACKLOG_FILE = config.DATA / "backlog.json"

OFFICIAL_FEEDS = {
    "OpenAI": "https://openai.com/news/rss.xml",
    "Google AI": "https://blog.google/technology/ai/rss/",
    "Google DeepMind": "https://deepmind.google/blog/rss.xml",
    "Microsoft AI": "https://blogs.microsoft.com/ai/feed/",
    "NVIDIA": "https://blogs.nvidia.com/feed/",
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
    "GitHub Changelog": "https://github.blog/changelog/feed/",
}
PRIMARY_DOMAINS = ["openai.com", "blog.google", "deepmind.google", "microsoft.com", "nvidia.com",
                   "anthropic.com", "meta.com", "huggingface.co", "github.com", "github.blog",
                   "ai.google.dev", "mistral.ai", "x.ai", "apple.com", "pib.gov.in", "meity.gov.in", "npci.org.in"]
# Credibility weight per source kind (added to the score)
KIND_WEIGHT = {"official": 6, "news": 2, "community": 2, "github": 3, "launch": 1, "search": 2, "backlog": 0}
TECH_WORDS = re.compile(r"\b(ai|a\.i\.|gpt|llm|model|chatgpt|claude|gemini|copilot|openai|anthropic|google|meta|"
                        r"nvidia|apple|iphone|android|samsung|pixel|app|software|robot|chip|cyber|hack|data|cloud|"
                        r"startup|upi|5g|isro|tech|agent|automation|deepseek|llama|mistral|grok|microsoft|tesla)\b", re.I)
AGE_LIMIT_HOURS = {"news": 24, "official": 72, "community": 30, "github": 24 * 7, "launch": 48, "search": 30}


# ---------------------------------------------------------------- helpers
def _get(url, **kw):
    kw.setdefault("timeout", 20)
    kw.setdefault("headers", UA)
    return requests.get(url, **kw)


def _parse_date(entry):
    for key in ("published_parsed", "updated_parsed"):
        if getattr(entry, key, None):
            return datetime.fromtimestamp(time.mktime(getattr(entry, key)), tz=timezone.utc)
    return None


def _clean_title(title):
    return title.rsplit(" - ", 1)[0].strip() if " - " in title else title.strip()


def _item(title, link, source, kind, summary="", published=None, country="GLOBAL", engagement=0):
    return {"title": _clean_title(title), "link": link, "source": source, "kind": kind,
            "summary": re.sub(r"<[^>]+>", " ", summary or "")[:600], "country": country,
            "published": published.isoformat() if published else None, "engagement": engagement}


def _rss(url, source, kind, country="GLOBAL"):
    out = []
    try:
        feed = feedparser.parse(_get(url).content)
        for e in feed.entries[:40]:
            src = (e.get("source") or {}).get("title") or source
            out.append(_item(e.title, e.link, src, kind, getattr(e, "summary", ""), _parse_date(e), country))
    except Exception as ex:  # noqa: BLE001
        print(f"[trends] {source} failed: {ex}")
    return out


# ---------------------------------------------------------------- sources
def from_google_news():
    items = []
    for c in config.COUNTRIES:
        items += _rss(f"https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-{c}&gl={c}&ceid={c}:en",
                      "Google News", "news", c)
        time.sleep(0.3)
    for q in config.SEARCH_QUERIES:
        items += _rss(f"https://news.google.com/rss/search?q={quote_plus(q + ' when:1d')}&hl=en-IN&gl=IN&ceid=IN:en",
                      "Google News", "news", "IN")
        time.sleep(0.3)
    return items


def from_official_blogs():
    items = []
    for name, url in OFFICIAL_FEEDS.items():
        for it in _rss(url, name, "official"):
            it["source"] = name
            items.append(it)
    return items


def from_hacker_news():
    try:
        hits = _get("https://hn.algolia.com/api/v1/search", params={"tags": "front_page", "hitsPerPage": 40}).json()["hits"]
        return [_item(h["title"], h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                      "Hacker News", "community", published=datetime.fromtimestamp(h["created_at_i"], tz=timezone.utc),
                      engagement=h.get("points", 0)) for h in hits if TECH_WORDS.search(h["title"])]
    except Exception as ex:  # noqa: BLE001
        print(f"[trends] Hacker News failed: {ex}")
        return []


def from_reddit():
    try:
        r = _get("https://www.reddit.com/r/artificial+OpenAI+LocalLLaMA+technology+singularity/top.json",
                 params={"t": "day", "limit": 40})
        posts = r.json()["data"]["children"]
        out = []
        for p in posts:
            d = p["data"]
            if d.get("over_18") or d.get("stickied"):
                continue
            link = d.get("url_overridden_by_dest") or f"https://www.reddit.com{d['permalink']}"
            out.append(_item(d["title"], link, f"r/{d['subreddit']}", "community", d.get("selftext", ""),
                             datetime.fromtimestamp(d["created_utc"], tz=timezone.utc), engagement=d.get("score", 0)))
        return out
    except Exception as ex:  # noqa: BLE001
        print(f"[trends] Reddit failed (often blocks cloud servers, that's fine): {ex}")
        return []


def from_github():
    since = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    headers = dict(UA)
    if os.getenv("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {os.getenv('GH_TOKEN')}"
    out = []
    try:
        for topic in ("llm", "ai-agents", "generative-ai"):
            r = _get("https://api.github.com/search/repositories", headers=headers,
                     params={"q": f"topic:{topic} created:>{since}", "sort": "stars", "order": "desc", "per_page": 5})
            for repo in r.json().get("items", []):
                if repo["stargazers_count"] < 200:
                    continue
                out.append(_item(f"{repo['full_name']}: {repo.get('description') or 'new open-source AI project'}",
                                 repo["html_url"], "GitHub", "github", repo.get("description") or "",
                                 datetime.fromisoformat(repo["created_at"].replace("Z", "+00:00")),
                                 engagement=repo["stargazers_count"]))
    except Exception as ex:  # noqa: BLE001
        print(f"[trends] GitHub failed: {ex}")
    return out


def from_product_hunt():
    return [i for i in _rss("https://www.producthunt.com/feed", "Product Hunt", "launch")
            if TECH_WORDS.search(i["title"] + " " + i["summary"])]


def from_google_trends():
    items = _rss("https://trends.google.com/trending/rss?geo=IN", "Google Trends India", "search", "IN")
    return [i for i in items if TECH_WORDS.search(i["title"] + " " + i["summary"])]


SOURCES = [from_google_news, from_official_blogs, from_hacker_news, from_reddit, from_github,
           from_product_hunt, from_google_trends]


def fetch_items():
    now = datetime.now(timezone.utc)
    items = []
    for src in SOURCES:
        got = src()
        print(f"[trends] {src.__name__}: {len(got)} items")
        items += got
    fresh = []
    for it in items + load_backlog():
        limit = AGE_LIMIT_HOURS.get(it["kind"], 48)
        if it["published"] and (now - datetime.fromisoformat(it["published"])) > timedelta(hours=limit):
            continue
        fresh.append(it)
    print(f"[trends] {len(fresh)} fresh items in total")
    return fresh


# ---------------------------------------------------------------- clustering & scoring
def cluster(items, threshold=72):
    clusters = []
    for it in items:
        for c in clusters:
            if fuzz.token_set_ratio(it["title"].lower(), c["title"].lower()) >= threshold:
                c["items"].append(it)
                if KIND_WEIGHT.get(it["kind"], 0) > KIND_WEIGHT.get(c["items"][0]["kind"], 0):
                    c["title"] = it["title"]  # prefer the most credible headline
                break
        else:
            clusters.append({"title": it["title"], "items": [it]})
    return clusters


def is_primary(url):
    host = urlparse(url or "").netloc.lower()
    return any(host == d or host.endswith("." + d) for d in PRIMARY_DOMAINS)


def _score(c):
    now = datetime.now(timezone.utc)
    kinds = {i["kind"] for i in c["items"]}
    countries = {i["country"] for i in c["items"]}
    sources = {i["source"] for i in c["items"] if i["source"]}
    ages = [(now - datetime.fromisoformat(i["published"])).total_seconds() / 3600
            for i in c["items"] if i["published"]]
    recency = max(0.0, 6 - min(ages) / 4) if ages else 1
    text = " ".join(i["title"] for i in c["items"]).lower()
    india = 4 if any(k in text for k in config.INDIA_KEYWORDS) else 0
    credibility = max(KIND_WEIGHT.get(k, 0) for k in kinds) + (3 if any(is_primary(i["link"]) for i in c["items"]) else 0)
    buzz = min(6, max(i.get("engagement", 0) for i in c["items"]) / 150)
    return round(len(countries) * 2 + len(sources) * 2 + len(kinds) * 2 + recency + india + credibility + buzz, 2)


def load_used():
    return json.loads(USED_FILE.read_text()) if USED_FILE.exists() else []


def save_used(title, category=None):
    used = load_used()
    used.append({"title": title, "category": category, "date": datetime.now(timezone.utc).date().isoformat()})
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    used = [u for u in used if u["date"] >= cutoff]
    USED_FILE.parent.mkdir(parents=True, exist_ok=True)
    USED_FILE.write_text(json.dumps(used, indent=2, ensure_ascii=False))


def recent_categories(n=3):
    return [u.get("category") for u in load_used()[-n:] if u.get("category")]


def _recently_used(title, used):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=config.REPEAT_WINDOW_DAYS)).date().isoformat()
    return any(u["date"] >= cutoff and same_story(title, u["title"]) for u in used)


def same_story(a, b):
    """Looser match used to keep near-duplicates of today's story out of the backlog and tomorrow's picks."""
    a, b = a.lower(), b.lower()
    shared = set(re.findall(r"[a-z0-9]{3,}", a)) & set(re.findall(r"[a-z0-9]{3,}", b))
    return fuzz.token_set_ratio(a, b) >= 60 or len(shared) >= 3 or (
        len(shared) >= 2 and any(w in shared for w in ("upi", "gpt", "gemini", "claude", "iphone", "nvidia", "isro")))


def load_backlog():
    if not BACKLOG_FILE.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    items = json.loads(BACKLOG_FILE.read_text())
    return [dict(i, kind="backlog") for i in items if datetime.fromisoformat(i["saved"]) > cutoff]


def save_backlog(clusters):
    """Keep runner-up stories (one item each) for the next 48 hours."""
    now = datetime.now(timezone.utc).isoformat()
    keep = [dict(c["items"][0], saved=now, title=c["title"]) for c in clusters]
    BACKLOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    BACKLOG_FILE.write_text(json.dumps(keep[:8], indent=2, ensure_ascii=False))


def top_clusters(n=12):
    clusters = cluster(fetch_items())
    used = load_used()
    clusters = [c for c in clusters if not _recently_used(c["title"], used)]
    for c in clusters:
        c["score"] = _score(c)
        c["kinds"] = sorted({i["kind"] for i in c["items"]})
        c["primary"] = any(is_primary(i["link"]) for i in c["items"])
    clusters.sort(key=lambda c: c["score"], reverse=True)
    return clusters[:n]


# ---------------------------------------------------------------- article text
def _decode(url):
    if "news.google.com" not in url:
        return url
    try:
        from googlenewsdecoder import gnewsdecoder
        res = gnewsdecoder(url, interval=1)
        if res.get("status"):
            return res["decoded_url"]
    except Exception as e:  # noqa: BLE001
        print(f"[trends] decode failed: {e}")
    return None


def article_text(cluster_):
    """Return (text, source_urls, primary_url). Reads up to 3 sources, primary/official sources first."""
    import trafilatura

    items = sorted(cluster_["items"], key=lambda i: (not is_primary(i["link"]), -KIND_WEIGHT.get(i["kind"], 0)))
    texts, urls, primary = [], [], None
    for it in items[:5]:
        real = _decode(it["link"])
        if not real or real in urls:
            continue
        urls.append(real)
        try:
            downloaded = trafilatura.fetch_url(real)
            body = trafilatura.extract(downloaded) if downloaded else None
            if body and len(body) > 300:
                texts.append(f"[Source: {it['source']} | {real}]\n{body[:4500]}")
                if is_primary(real) and not primary:
                    primary = real
        except Exception as e:  # noqa: BLE001
            print(f"[trends] extract failed {real}: {e}")
        if len(texts) >= 3:
            break
    if not texts:
        texts = ["\n".join(f"[{i['source']}] {i['title']}: {i['summary']}" for i in cluster_["items"])]
    return "\n\n".join(texts), urls[:5], primary
