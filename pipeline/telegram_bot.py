"""Minimal Telegram Bot API client.

Uses polling (getUpdates), so no webhook or public URL is needed.
Do not advance the update offset here; pipeline.gate.py also reads
Telegram callback updates.
"""

import json
import requests

from . import config

API = "https://api.telegram.org/bot{token}/{method}"


def _call(method, files=None, timeout=120, **params):
    if not config.TELEGRAM_BOT_TOKEN:
        print(f"[telegram] token missing, skipped {method}")
        return None

    url = API.format(
        token=config.TELEGRAM_BOT_TOKEN,
        method=method,
    )

    response = requests.post(
        url,
        data=params,
        files=files,
        timeout=timeout,
    )
    response.raise_for_status()

    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram {method} failed: {data}"
        )

    return data["result"]


def send_message(text, reply_markup=None):
    params = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text[:4000],
        "disable_web_page_preview": "true",
    }

    if reply_markup:
        params["reply_markup"] = json.dumps(reply_markup)

    return _call("sendMessage", **params)


def notify(text):
    """Best-effort notification that never raises."""
    try:
        send_message(text)
    except Exception as e:
        print(f"[telegram] notify failed: {e}")


def send_video_for_approval(video_path, caption, job_id):
    keyboard = {
        "inline_keyboard": [[
            {
                "text": "✅ Approve",
                "callback_data": f"approve:{job_id}",
            },
            {
                "text": "❌ Reject",
                "callback_data": f"reject:{job_id}",
            },
        ]]
    }

    with open(video_path, "rb") as video:
        return _call(
            "sendVideo",
            files={
                "video": ("short.mp4", video, "video/mp4"),
            },
            timeout=600,
            chat_id=config.TELEGRAM_CHAT_ID,
            caption=caption[:1000],
            supports_streaming="true",
            reply_markup=json.dumps(keyboard),
        )


def get_decision(job_id):
    """Return the latest matching approval decision without advancing
    Telegram's update offset.
    """
    updates = _call(
        "getUpdates",
        timeout=60,
        allowed_updates=json.dumps(["callback_query"]),
        limit=100,
    ) or []

    decision = None

    for update in updates:
        callback = update.get("callback_query")
        if not callback:
            continue

        message = callback.get("message") or {}
        chat = message.get("chat") or {}

        # Only accept button presses from the configured chat.
        if str(chat.get("id")) != str(config.TELEGRAM_CHAT_ID):
            continue

        action, _, callback_job_id = (
            callback.get("data") or ""
        ).partition(":")

        if (
            callback_job_id == job_id
            and action in ("approve", "reject")
        ):
            decision = action

            try:
                _call(
                    "answerCallbackQuery",
                    callback_query_id=callback["id"],
                    text=f"Got it: {action}",
                )
            except Exception as e:
                print(
                    f"[telegram] callback acknowledgement failed: {e}"
                )

    return decision