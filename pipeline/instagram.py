"""Publish a Reel to an Instagram professional account with the Instagram Graph API
(Facebook Login for Business). Uses a resumable upload, so the video does not need a public URL.

Needs:  IG_USER_ID       - Instagram business account id (see README "Instagram setup")
        IG_ACCESS_TOKEN  - long-lived Page access token with instagram_content_publish
"""
import time

import requests

from . import config

GRAPH = "https://graph.facebook.com/v21.0"


def enabled():
    return bool(config.IG_USER_ID and config.IG_ACCESS_TOKEN)


def _check(resp):
    data = resp.json()
    if resp.status_code >= 400 or "error" in data:
        raise RuntimeError(f"Instagram API error: {data.get('error', data)}")
    return data


def username():
    r = requests.get(f"{GRAPH}/{config.IG_USER_ID}", params={
        "fields": "username", "access_token": config.IG_ACCESS_TOKEN}, timeout=30)
    return _check(r)["username"]


def build_caption(meta):
    """Instagram captions: max 2,200 characters and 30 hashtags. Links aren't clickable, so keep it short."""
    hashtags = []
    for h in meta.get("hashtags", []) + ["#technews", "#ainews", "#techindia", "#reels"]:
        h = h if h.startswith("#") else f"#{h}"
        if h.lower() != "#shorts" and h.lower() not in [x.lower() for x in hashtags]:
            hashtags.append(h)
    summary = meta["description"].split("\nSources:")[0].split("\n\nThis video uses")[0].strip()
    from urllib.parse import urlparse
    domains = []
    for u in meta.get("sources", []):
        d = urlparse(u).netloc.replace("www.", "")
        if d and d not in domains:
            domains.append(d)
    sources = f"Source: {', '.join(domains[:3])}\n" if domains else ""
    caption = (f"{meta['title'].replace('#shorts', '').strip()}\n\n{summary}\n\n"
               f"💬 What do you think? Comment below.\n"
               f"{sources}"
               f"AI-generated narration and visuals.\n\n{' '.join(hashtags[:20])}")
    return caption[:2150]


def publish_reel(video_path, caption):
    token = config.IG_ACCESS_TOKEN
    headers = {"Authorization": f"OAuth {token}"}

    # 1. Create a resumable Reel container
    container = _check(requests.post(f"{GRAPH}/{config.IG_USER_ID}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption,
        "share_to_feed": "true", "thumb_offset": "0", "access_token": token,
    }, timeout=60))
    cid = container["id"]
    upload_uri = container.get("uri") or f"https://rupload.facebook.com/ig-api-upload/v21.0/{cid}"

    # 2. Upload the file bytes
    data = open(video_path, "rb").read()
    up = requests.post(upload_uri, data=data, timeout=600, headers={
        **headers, "offset": "0", "file_size": str(len(data))})
    if up.status_code >= 400 or not up.json().get("success"):
        raise RuntimeError(f"Instagram upload failed: {up.text[:500]}")
    print("[instagram] upload complete, waiting for processing")

    # 3. Wait until Instagram has processed the video
    for _ in range(60):  # up to ~10 minutes
        st = _check(requests.get(f"{GRAPH}/{cid}", params={"fields": "status_code,status"},
                                 headers=headers, timeout=30))
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram processing failed: {st}")
        time.sleep(10)
    else:
        raise RuntimeError("Instagram processing timed out")

    # 4. Publish
    media_id = _check(requests.post(f"{GRAPH}/{config.IG_USER_ID}/media_publish",
                                    data={"creation_id": cid}, headers=headers, timeout=60))["id"]
    link = _check(requests.get(f"{GRAPH}/{media_id}", params={"fields": "permalink"},
                               headers=headers, timeout=30)).get("permalink", "")
    print(f"[instagram] published {media_id} {link}")
    return media_id, link
