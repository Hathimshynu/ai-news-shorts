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


class IGError(RuntimeError):
    def __init__(self, err):
        self.code = err.get("code") if isinstance(err, dict) else None
        self.subcode = err.get("error_subcode") if isinstance(err, dict) else None
        msg = err.get("error_user_msg") or err.get("message") if isinstance(err, dict) else str(err)
        super().__init__(f"Instagram API error {self.code}/{self.subcode}: {msg}")


HINTS = {
    190: "Instagram token expired or was revoked: run scripts/instagram_token.py again and update IG_ACCESS_TOKEN.",
    10: "Missing permission: the token needs instagram_content_publish (re-create it in Graph API Explorer).",
    200: "Missing permission: the token needs instagram_content_publish (re-create it in Graph API Explorer).",
    4: "Instagram rate limit: it retries automatically later.", 17: "Instagram rate limit: retries later.",
    32: "Instagram rate limit: retries later.", 613: "Instagram rate limit: retries later.",
    "LIMIT": "Instagram's 24-hour posting limit is used up: it works again within 24 hours.",
    2207026: "Instagram rejected the video format.", "PROCESSING_ERROR": "Instagram couldn't process the video file.",
    "TIMEOUT": "Instagram was very slow to process the video: it retries later.",
}


def hint(err):
    """Plain-English next step for an Instagram error (shown in Telegram)."""
    return HINTS.get(getattr(err, "code", None)) or HINTS.get(getattr(err, "subcode", None)) or ""


# Temporary problems worth waiting for: "media not ready yet", rate limits, Meta server errors
RETRYABLE = {9007, 2207027, 2207001, 2207003, 2207008, 2207020, 4, 17, 32, 613, 1, 2, -1}


def _check(resp):
    try:
        data = resp.json()
    except ValueError:
        raise IGError({"code": resp.status_code, "message": resp.text[:300]})
    if resp.status_code >= 400 or "error" in data:
        raise IGError(data.get("error", data))
    return data


def username():
    r = requests.get(f"{GRAPH}/{config.IG_USER_ID}", params={
        "fields": "username", "access_token": config.IG_ACCESS_TOKEN}, timeout=30)
    return _check(r)["username"]


def build_caption(meta):
    """Instagram/Facebook caption: hook line, what you'll learn, a discussion question, one CTA,
    source attribution, hashtags. Max 2,200 characters; links aren't clickable so we name the sources."""
    from urllib.parse import urlparse
    hashtags = []
    for h in meta.get("hashtags", []) + ["#technews", "#ainews", "#techindia"]:
        h = h if h.startswith("#") else f"#{h}"
        if h.lower() != "#shorts" and h.lower() not in [x.lower() for x in hashtags]:
            hashtags.append(h)
    summary = meta.get("summary") or meta["description"].split("\nSources:")[0].split("\nRelated:")[0].strip()
    domains = []
    for u in meta.get("sources", []):
        d = urlparse(u).netloc.replace("www.", "")
        if d and d not in domains:
            domains.append(d)
    parts = [meta.get("ig_opening") or meta["title"].replace("#shorts", "").strip(), "", summary, ""]
    parts.append(f"💬 {meta.get('ig_question') or 'What do you think? Tell me in the comments.'}")
    if config.CHANNEL_HANDLE:
        parts.append(f"Follow {config.CHANNEL_HANDLE} for daily AI updates.")
    if domains:
        parts.append(f"Source: {', '.join(domains[:3])}")
    parts += ["AI-generated narration and visuals.", "", " ".join(hashtags[:12])]
    return "\n".join(parts)[:2150]


def _limit_left():
    """How many API posts are left in Instagram's rolling 24-hour publishing limit (None if unknown)."""
    try:
        d = _check(requests.get(f"{GRAPH}/{config.IG_USER_ID}/content_publishing_limit", params={
            "fields": "config,quota_usage", "access_token": config.IG_ACCESS_TOKEN}, timeout=30))["data"][0]
        return d["config"]["quota_total"] - d["quota_usage"]
    except Exception:  # noqa: BLE001 - informational only
        return None


def _upload_and_wait(video_path, caption, headers, token):
    container = _check(requests.post(f"{GRAPH}/{config.IG_USER_ID}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption,
        "share_to_feed": "true", "thumb_offset": "500", "access_token": token,
    }, timeout=60))
    cid = container["id"]
    upload_uri = container.get("uri") or f"https://rupload.facebook.com/ig-api-upload/v21.0/{cid}"
    data = open(video_path, "rb").read()
    up = requests.post(upload_uri, data=data, timeout=600, headers={
        **headers, "offset": "0", "file_size": str(len(data))})
    if up.status_code >= 400 or not up.json().get("success"):
        raise IGError({"code": up.status_code, "message": f"upload failed: {up.text[:300]}"})
    print("[instagram] upload complete, waiting for processing")
    for _ in range(90):  # up to ~15 minutes
        st = _check(requests.get(f"{GRAPH}/{cid}", params={"fields": "status_code,status"},
                                 headers=headers, timeout=30))
        code = st.get("status_code")
        if code == "FINISHED":
            return cid
        if code in ("ERROR", "EXPIRED"):
            raise IGError({"code": "PROCESSING_" + code, "message": st.get("status", "")})
        time.sleep(10)
    raise IGError({"code": "TIMEOUT", "message": "Instagram took more than 15 minutes to process the video"})


def publish_reel(video_path, caption):
    """Upload, wait for processing, publish. Retries temporary Instagram problems; one fresh
    re-upload if Instagram's processing fails. Raises IGError with Meta's error code otherwise."""
    token = config.IG_ACCESS_TOKEN
    headers = {"Authorization": f"OAuth {token}"}
    left = _limit_left()
    if left is not None:
        print(f"[instagram] API posts left in the last 24 h: {left}")
        if left <= 0:
            raise IGError({"code": "LIMIT", "message": "Instagram's 24-hour API posting limit is used up; "
                                                        "it will work again within 24 hours"})
    last = None
    for upload_try in range(2):
        try:
            cid = _upload_and_wait(video_path, caption, headers, token)
        except IGError as e:
            last = e
            if str(e.code).startswith("PROCESSING") and upload_try == 0:
                print(f"[instagram] {e} -> uploading again")
                time.sleep(20)
                continue
            raise
        for publish_try in range(6):  # "media not ready" is common right after FINISHED
            try:
                media_id = _check(requests.post(f"{GRAPH}/{config.IG_USER_ID}/media_publish",
                                                data={"creation_id": cid}, headers=headers, timeout=60))["id"]
                break
            except IGError as e:
                last = e
                if e.code not in RETRYABLE and e.subcode not in RETRYABLE:
                    raise
                print(f"[instagram] {e} -> waiting and retrying publish")
                time.sleep(20 * (publish_try + 1))
        else:
            raise last
        link = ""
        try:
            link = _check(requests.get(f"{GRAPH}/{media_id}", params={"fields": "permalink"},
                                       headers=headers, timeout=30)).get("permalink", "")
        except IGError as e:
            print(f"[instagram] posted, but could not read the link: {e}")
        print(f"[instagram] published {media_id} {link}")
        return media_id, link
    raise last
