"""Upload a Short to YouTube with the Data API v3 using a stored OAuth refresh token."""
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from . import config

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]


def client():
    creds = Credentials(
        token=None,
        refresh_token=config.YT_REFRESH_TOKEN,
        client_id=config.YT_CLIENT_ID,
        client_secret=config.YT_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def channel_title(yt=None):
    yt = yt or client()
    items = yt.channels().list(part="snippet", mine=True).execute().get("items", [])
    return items[0]["snippet"]["title"] if items else None


def upload(video_path, title, description, tags, thumbnail_path=None, privacy=None):
    yt = client()
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": tags[:30],
            "categoryId": config.YT_CATEGORY_ID,
            "defaultLanguage": "en-IN",
            "defaultAudioLanguage": "en-IN",
        },
        "status": {
            "privacyStatus": privacy or config.YT_PRIVACY,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,  # AI disclosure
        },
    }

    def _insert(b):
        media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
        req = yt.videos().insert(part="snippet,status", body=b, media_body=media)
        resp = None
        while resp is None:
            status, resp = req.next_chunk()
            if status:
                print(f"[youtube] uploaded {int(status.progress() * 100)}%")
        return resp

    try:
        resp = _insert(body)
    except HttpError as e:
        if "containsSyntheticMedia" in str(e):
            body["status"].pop("containsSyntheticMedia")
            print("[youtube] API rejected containsSyntheticMedia; retrying without it. Tick the AI label in Studio.")
            resp = _insert(body)
        else:
            raise
    video_id = resp["id"]
    print(f"[youtube] video id {video_id}")

    if thumbnail_path:
        try:
            yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail_path))).execute()
        except HttpError as e:  # needs a phone-verified channel; not fatal
            print(f"[youtube] thumbnail not set: {e}")
    return video_id
