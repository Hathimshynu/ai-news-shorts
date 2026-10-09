"""Publish a Reel to a Facebook Page (Graph API video_reels: start -> upload -> finish).

Needs:  FB_PAGE_ID     - your Page id (printed by scripts/instagram_token.py)
        FB_PAGE_TOKEN  - Page access token with pages_manage_posts. Optional: if not set,
                         IG_ACCESS_TOKEN is used (it is the same Page token).
Meta limit: 30 API posts per Page per 24 hours.
"""
import time
from pathlib import Path

import requests

from . import config

GRAPH = "https://graph.facebook.com/v21.0"


def _token():
    return config.FB_PAGE_TOKEN or config.IG_ACCESS_TOKEN


def enabled():
    return bool(config.FB_PAGE_ID and _token())


def _check(resp):
    data = resp.json()
    if resp.status_code >= 400 or "error" in data:
        raise RuntimeError(f"Facebook API error: {data.get('error', data)}")
    return data


def page_name():
    r = requests.get(f"{GRAPH}/{config.FB_PAGE_ID}", params={"fields": "name", "access_token": _token()}, timeout=30)
    return _check(r)["name"]


def build_description(meta):
    """Facebook shows links as plain text in Reels; keep it close to the Instagram caption."""
    from .instagram import build_caption
    return build_caption(meta)


def publish_reel(video_path, description, cover_path=None):
    token, page = _token(), config.FB_PAGE_ID

    # 1. Start an upload session
    start = _check(requests.post(f"{GRAPH}/{page}/video_reels",
                                 data={"upload_phase": "start", "access_token": token}, timeout=60))
    video_id = start["video_id"]
    upload_url = start.get("upload_url") or f"https://rupload.facebook.com/video-upload/v21.0/{video_id}"

    # 2. Upload the file
    data = open(video_path, "rb").read()
    up = requests.post(upload_url, data=data, timeout=600, headers={
        "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data)),
        "Content-Type": "application/octet-stream"})
    if up.status_code >= 400 or not up.json().get("success"):
        raise RuntimeError(f"Facebook upload failed: {up.text[:500]}")

    # 3. Publish
    _check(requests.post(f"{GRAPH}/{page}/video_reels", data={
        "access_token": token, "video_id": video_id, "upload_phase": "finish",
        "video_state": "PUBLISHED", "description": description}, timeout=60))

    # 4. Custom cover image (best-effort; the Reel is already published if this fails)
    if cover_path and Path(cover_path).exists():
        try:
            with open(cover_path, "rb") as img:
                _check(requests.post(f"{GRAPH}/{video_id}/thumbnails", timeout=60,
                                     data={"access_token": token, "is_preferred": "true"},
                                     files={"source": ("cover.jpg", img, "image/jpeg")}))
        except Exception as e:  # noqa: BLE001
            print(f"[facebook] cover not set: {e}")

    # 5. Wait briefly for processing so we can report problems (publishing continues either way)
    for _ in range(30):  # up to ~5 minutes
        st = _check(requests.get(f"{GRAPH}/{video_id}", params={"fields": "status", "access_token": token},
                                 timeout=30)).get("status", {})
        if st.get("video_status") == "error":
            raise RuntimeError(f"Facebook processing failed: {st}")
        if st.get("video_status") == "ready" or st.get("publishing_phase", {}).get("status") == "complete":
            break
        time.sleep(10)
    link = f"https://www.facebook.com/reel/{video_id}"
    print(f"[facebook] published {link}")
    return video_id, link
