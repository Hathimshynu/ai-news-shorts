"""Collect performance numbers for every published video and store them in data/analytics.json.

What each platform gives with the permissions this project uses:
  YouTube    views, likes, comments                    (Data API; retention needs the separate YouTube Analytics API)
  Instagram  likes, comments; reach/views/saves/shares if the token has instagram_manage_insights
  Facebook   likes, comments; plays/reach if the token has read_insights
Engagement rate = (likes + comments + shares + saves) / views x 100   (only when views are known)
"""
import json
from datetime import datetime, timezone

import requests

from . import config, gate, youtube

FILE = config.DATA / "analytics.json"
GRAPH = "https://graph.facebook.com/v21.0"


def _graph(path, token, **params):
    r = requests.get(f"{GRAPH}/{path}", params={"access_token": token, **params}, timeout=30)
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("message", data["error"]))
    return data


def _instagram(media_id):
    tok = config.IG_ACCESS_TOKEN
    d = _graph(media_id, tok, fields="like_count,comments_count")
    m = {"likes": d.get("like_count", 0), "comments": d.get("comments_count", 0)}
    for metrics in ("views,reach,saved,shares", "reach,saved,shares"):
        try:
            for row in _graph(f"{media_id}/insights", tok, metric=metrics).get("data", []):
                m[{"saved": "saves"}.get(row["name"], row["name"])] = row["values"][0]["value"]
            break
        except Exception as e:  # noqa: BLE001
            m["note"] = f"insights unavailable: {str(e)[:80]}"
    return m


def _facebook(video_id):
    tok = config.FB_PAGE_TOKEN or config.IG_ACCESS_TOKEN
    d = _graph(video_id, tok, fields="likes.summary(true),comments.summary(true)")
    m = {"likes": d.get("likes", {}).get("summary", {}).get("total_count", 0),
         "comments": d.get("comments", {}).get("summary", {}).get("total_count", 0)}
    try:
        for row in _graph(f"{video_id}/video_insights", tok,
                          metric="blue_reels_play_count,post_impressions_unique").get("data", []):
            key = {"blue_reels_play_count": "views", "post_impressions_unique": "reach"}.get(row["name"], row["name"])
            m[key] = row["values"][0]["value"]
    except Exception as e:  # noqa: BLE001
        m["note"] = f"insights unavailable: {str(e)[:80]}"
    return m


def engagement_rate(m):
    views = m.get("views") or 0
    if not views:
        return None
    return round((m.get("likes", 0) + m.get("comments", 0) + m.get("shares", 0) + m.get("saves", 0)) / views * 100, 2)


def load():
    return json.loads(FILE.read_text()) if FILE.exists() else {}


def collect():
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    data = load()
    records = {j: r for j, r in gate.load_all().items() if isinstance(r, dict) and r.get("ids")}

    yt_ids = {r["ids"]["youtube"]: j for j, r in records.items() if r["ids"].get("youtube")}
    try:
        yt = youtube.stats(list(yt_ids))
    except Exception as e:  # noqa: BLE001
        print(f"[analytics] youtube failed: {e}")
        yt = {}

    for job_id, rec in records.items():
        entry = data.setdefault(job_id, {"history": []})
        entry["info"] = rec.get("info", {})
        entry["links"] = rec.get("platforms", {})
        plats = entry.setdefault("platforms", {})
        if rec["ids"].get("youtube") in yt:
            plats["youtube"] = yt[rec["ids"]["youtube"]]
        for name, fn, ok in (("instagram", _instagram, config.IG_ACCESS_TOKEN),
                             ("facebook", _facebook, config.FB_PAGE_TOKEN or config.IG_ACCESS_TOKEN)):
            if rec["ids"].get(name) and ok:
                try:
                    plats[name] = fn(rec["ids"][name])
                except Exception as e:  # noqa: BLE001
                    print(f"[analytics] {name} {job_id} failed: {e}")
        for m in plats.values():
            m["engagement_rate"] = engagement_rate(m)
        total = sum((m.get("views") or 0) for m in plats.values())
        entry["history"] = [h for h in entry["history"] if h["date"] != today][-29:] + [{"date": today, "views": total}]
        entry["fetched"] = now.isoformat()
    FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"[analytics] updated {len(records)} videos")
    return data


if __name__ == "__main__":
    collect()
