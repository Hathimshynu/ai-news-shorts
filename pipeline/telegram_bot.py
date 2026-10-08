"""Minimal Telegram Bot API client. Uses polling (getUpdates), so no webhook or public URL
is needed. Never set a webhook on this bot, or getUpdates stops working."""
import json
import requests

from . import config

API = "https://api.telegram.org/bot{token}/{method}"


def _call(method, files=None, timeout=120, **params):
    if not config.TELEGRAM_BOT_TOKEN:
        print(f"[telegram] token missing, skipped {method}")
        return None
    url = API.format(token=config.TELEGRAM_BOT_TOKEN, method=method)
    r = requests.post(url, data=params, files=files, timeout=timeout)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")
    return data["result"]


def send_message(text, reply_markup=None):
    params = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4000], "disable_web_page_preview": "true"}
    if reply_markup:
        params["reply_markup"] = json.dumps(reply_markup)
    return _call("sendMessage", **params)


def notify(text):
    """Best-effort notification that never raises (used in error paths)."""
    try:
        send_message(text)
    except Exception as e:  # noqa: BLE001
        print(f"[telegram] notify failed: {e}")


def send_video_for_approval(video_path, caption, job_id):
    keyboard = {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"approve:{job_id}"},
        {"text": "❌ Reject", "callback_data": f"reject:{job_id}"},
    ]]}
    with open(video_path, "rb") as f:
        return _call(
            "sendVideo",
            files={"video": ("short.mp4", f, "video/mp4")},
            timeout=600,
            chat_id=config.TELEGRAM_CHAT_ID,
            caption=caption[:1000],
            supports_streaming="true",
            reply_markup=json.dumps(keyboard),
        )


def get_decision(job_id):
    """Return 'approve', 'reject' or None for this job, based on button presses.
    Telegram keeps updates for 24 hours, which covers the gap between workflows."""
    updates = _call("getUpdates", timeout=60, allowed_updates=json.dumps(["callback_query"])) or []
    decision, last_id = None, None
    for u in updates:
        last_id = u["update_id"]
        cq = u.get("callback_query")
        if not cq:
            continue
        # Only accept presses from your own chat.
        if str(cq.get("message", {}).get("chat", {}).get("id")) != str(config.TELEGRAM_CHAT_ID):
            continue
        action, _, jid = (cq.get("data") or "").partition(":")
        if jid == job_id and action in ("approve", "reject"):
            decision = action  # latest press wins
            try:
                _call("answerCallbackQuery", callback_query_id=cq["id"], text=f"Got it: {action}")
            except Exception:  # noqa: BLE001 - old callbacks can't be answered; harmless
                pass
    if last_id is not None and decision:
        _call("getUpdates", offset=last_id + 1, timeout=60)  # mark as read
    return decision
