"""Find today's trending tech stories for India + Asia from Google News RSS,
cluster duplicates across countries, score them, and fetch article text."""
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import feedparser
import requests
from rapidfuzz import fuzz

from . import config

UA = {"User-Agent": "Mozilla/5.0 (compatible; ai-news-shorts/1.0)"}
USED_FILE = config.DATA / "used_topics.json"


def _feed_urls():
    urls = []
    for c in config.COUNTRIES:
        urls.append((c, f"https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-{c}&gl={c}&ceid={c}:en"))
    for q in config.SEARCH_QUERIES:
        urls.append(("IN", f"https://news.google.com/rss/search?q={quote_plus(q + ' when:1d')}&hl=en-IN&gl=IN&ceid=IN:en"))
    return urls


def _clean_title(title):
    # Google News titles end with " - Publisher"
    return title.rsplit(" - ", 1)[0].strip() if " - " in title else title.strip()


def fetch_items():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    items = []
    for country, url in _feed_urls():
        try:
            resp = requests.get(url, headers=UA, timeout=20)
            feed = feedparser.parse(resp.content)
        except Exception as e:  # noqa: BLE001
            print(f"[trends] feed failed {url}: {e}")
            continue
        for e in feed.entries:
            published = None
            if getattr(e, "published_parsed", None):
                published = datetime.fromtimestamp(time.mktime(e.published_parsed), tz=timezone.utc)
            if published and published < cutoff:
                continue
            items.append({
                "title": _clean_title(e.title),
                "link": e.link,
                "source": (e.get("source") or {}).get("title", ""),
                "summary": getattr(e, "summary", ""),
                "country": country,
                "published": published.isoformat() if published else None,
            })
        time.sleep(0.5)  # be polite to Google News
    print(f"[trends] fetched {len(items)} items")
    return items


def cluster(items, threshold=72):
    clusters = []
    for it in items:
        for c in clusters:
            if fuzz.token_set_ratio(it["title"].lower(), c["title"].lower()) >= threshold:
                c["items"].append(it)
                break
        else:
            clusters.append({"title": it["title"], "items": [it]})
    return clusters


def _score(c):
    countries = {i["country"] for i in c["items"]}
    sources = {i["source"] for i in c["items"] if i["source"]}
    now = datetime.now(timezone.utc)
    ages = [(now - datetime.fromisoformat(i["published"])).total_seconds() / 3600
            for i in c["items"] if i["published"]]
    recency = max(0.0, 6 - min(ages) / 4) if ages else 0  # up to +6 for very fresh stories
    text = " ".join(i["title"] for i in c["items"]).lower()
    india = 4 if any(k in text for k in config.INDIA_KEYWORDS) else 0
    return len(countries) * 3 + len(sources) * 2 + len(c["items"]) + recency + india


def load_used():
    if USED_FILE.exists():
        return json.loads(USED_FILE.read_text())
    return []


def save_used(title):
    used = load_used()
    used.append({"title": title, "date": datetime.now(timezone.utc).date().isoformat()})
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    used = [u for u in used if u["date"] >= cutoff]
    USED_FILE.parent.mkdir(parents=True, exist_ok=True)
    USED_FILE.write_text(json.dumps(used, indent=2))


def _recently_used(title, used):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=config.REPEAT_WINDOW_DAYS)).date().isoformat()
    return any(u["date"] >= cutoff and fuzz.token_set_ratio(title.lower(), u["title"].lower()) >= 70
               for u in used)


def top_clusters(n=10):
    clusters = cluster(fetch_items())
    used = load_used()
    clusters = [c for c in clusters if not _recently_used(c["title"], used)]
    for c in clusters:
        c["score"] = round(_score(c), 2)
    clusters.sort(key=lambda c: c["score"], reverse=True)
    return clusters[:n]


def _decode(url):
    try:
        from googlenewsdecoder import gnewsdecoder
        res = gnewsdecoder(url, interval=1)
        if res.get("status"):
            return res["decoded_url"]
    except Exception as e:  # noqa: BLE001
        print(f"[trends] decode failed: {e}")
    return None


def article_text(cluster_):
    """Return (text, source_urls). Falls back to RSS snippets if pages can't be read."""
    import trafilatura

    texts, urls = [], []
    for it in cluster_["items"][:3]:
        real = _decode(it["link"])
        if not real:
            continue
        urls.append(real)
        try:
            downloaded = trafilatura.fetch_url(real)
            body = trafilatura.extract(downloaded) if downloaded else None
            if body and len(body) > 400:
                texts.append(body[:6000])
        except Exception as e:  # noqa: BLE001
            print(f"[trends] extract failed {real}: {e}")
        if texts:
            break
    if not texts:
        import re
        snippets = [re.sub(r"<[^>]+>", " ", i["summary"]) for i in cluster_["items"]]
        texts = ["\n".join(i["title"] for i in cluster_["items"]) + "\n" + "\n".join(snippets)]
    return "\n\n".join(texts), urls
