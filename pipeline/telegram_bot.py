"""Minimal Telegram Bot API client.

Uses polling (getUpdates), so no webhook or public URL is needed.
Never advance the update offset here: pipeline/gate.py reads the same
callback updates to find Approve/Reject presses.
"""
import json

import requests

from . import config

API = "https://api.telegram.org/bot{token}/{method}"


def _call(method, files=None, timeout=120, **params):
    if not config.TELEGRAM_BOT_TOKEN:
        print(f"[telegram] token missing, skipped {method}")
        return None
    url = API.format(token=config.TELEGRAM_BOT_TOKEN, method=method)
    response = requests.post(url, data=params, files=files, timeout=timeout)
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")
    return data["result"]


def send_message(text, reply_markup=None):
    params = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4000], "disable_web_page_preview": "true"}
    if reply_markup:
        params["reply_markup"] = json.dumps(reply_markup)
    return _call("sendMessage", **params)


def notify(text):
    """Best-effort notification that never raises."""
    try:
        send_message(text)
    except Exception as e:  # noqa: BLE001
        print(f"[telegram] notify failed: {e}")


def send_video_for_approval(video_path, caption, job_id, post_label=None):
    approve = f"✅ Approve · posts {post_label}" if post_label else "✅ Approve"
    keyboard = {"inline_keyboard": [
        [{"text": approve, "callback_data": f"approve:{job_id}"}],
        [{"text": "⚡ Post now", "callback_data": f"now:{job_id}"},
         {"text": "❌ Reject", "callback_data": f"reject:{job_id}"}],
    ]}
    with open(video_path, "rb") as video:
        return _call(
            "sendVideo",
            files={"video": ("short.mp4", video, "video/mp4")},
            timeout=600,
            chat_id=config.TELEGRAM_CHAT_ID,
            caption=caption[:1000],
            supports_streaming="true",
            reply_markup=json.dumps(keyboard),
        )
