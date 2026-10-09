"""Cheap approval check that runs every 15 minutes during each posting window.
Standard library only, so it starts in seconds without installing anything.

Decides one action for the latest video and writes it to $GITHUB_OUTPUT:
  publish  - approved (or a previous post was interrupted) -> install packages and post
  done     - already posted / rejected / expired, or no video -> nothing to do
  wait     - no decision yet -> check again in 15 minutes

MODE (set by the workflow):
  check   - scheduled check inside the window: stay silent while waiting
  final   - last check of the window: if still not approved, tell you it was skipped
  manual  - you ran the workflow by hand: posts right away if approved, otherwise reports status

data/published.json keeps one record per video, e.g.
  {"2026-10-09-evening": {"status": "posting", "platforms": {"youtube": "https://..."}, "attempts": 1}}
so a video (or a platform) is never posted twice, however many times Approve is pressed.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
META = ROOT / "out" / "meta.json"
DONE = ROOT / "data" / "published.json"
TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
CHAT = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
MODE = os.getenv("MODE", "check")
FINAL_STATES = ("published", "rejected", "expired")
MAX_ATTEMPTS = 3


def tg(method, **params):
    data = urllib.parse.urlencode(params).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/{method}", data, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}") or {"ok": False, "description": str(e)}


def notify(text):
    try:
        tg("sendMessage", chat_id=CHAT, text=text[:4000])
    except Exception as e:  # noqa: BLE001
        print(f"[gate] notify failed: {e}")


# ---------- published.json ----------
def load_all():
    return json.loads(DONE.read_text()) if DONE.exists() else {}


def get(job_id):
    rec = load_all().get(job_id)
    if isinstance(rec, str):  # old format: plain status string
        rec = {"status": rec, "platforms": {}}
    return rec or {"status": "new", "platforms": {}}


def put(job_id, rec):
    done = load_all()
    done[job_id] = rec
    done = dict(sorted(done.items())[-60:])  # keep the last ~30 days
    DONE.parent.mkdir(parents=True, exist_ok=True)
    DONE.write_text(json.dumps(done, indent=2, ensure_ascii=False) + "\n")


def mark_done(job_id, status):
    rec = get(job_id)
    rec["status"] = status
    put(job_id, rec)


# ---------- Telegram buttons ----------
def _matches_me(cq):
    msg_chat = str(((cq.get("message") or {}).get("chat") or {}).get("id", "")).strip()
    from_id = str((cq.get("from") or {}).get("id", "")).strip()
    return CHAT in (msg_chat, from_id)


def decision_for(job_id):
    """Latest Approve/Reject press for this video, without consuming Telegram updates.
    Returns (decision, [(chat_id, message_id), ...] of the approval messages)."""
    res = tg("getUpdates", allowed_updates=json.dumps(["callback_query"]), limit=100)
    if not res.get("ok"):
        print(f"[gate] Telegram API error: {res}")
        return None, []
    decision, messages = None, []
    for u in res.get("result", []):
        cq = u.get("callback_query") or {}
        action, _, jid = (cq.get("data") or "").partition(":")
        mine = _matches_me(cq)
        print(f"[gate] update={u.get('update_id')} data={cq.get('data')!r} mine={mine} this_job={jid == job_id}")
        if not mine or jid != job_id or action not in ("approve", "reject"):
            continue
        decision = action  # latest press wins
        msg = cq.get("message") or {}
        if msg.get("message_id"):
            messages.append((msg["chat"]["id"], msg["message_id"]))
    return decision, messages


def lock_buttons(messages, label):
    """Replace Approve/Reject with a status label so the video can't be approved twice."""
    markup = json.dumps({"inline_keyboard": [[{"text": label, "callback_data": "noop"}]]})
    for chat_id, message_id in set(messages):
        res = tg("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup=markup)
        if not res.get("ok") and "not modified" not in str(res.get("description", "")):
            print(f"[gate] could not update buttons: {res}")


# ---------- decision ----------
def decide():
    if not META.exists():
        if MODE == "manual":
            notify("ℹ️ No video from the last 8 hours to publish.")
        return "done"
    meta = json.loads(META.read_text())
    job_id, title = meta["job_id"], meta["title"]
    rec = get(job_id)

    if rec["status"] in FINAL_STATES:
        print(f"[gate] {job_id} already handled: {rec['status']}")
        return "done"
    if rec["status"] == "posting":  # a previous run was interrupted or one platform failed
        if rec.get("attempts", 0) >= MAX_ATTEMPTS:
            mark_done(job_id, "published")
            return "done"
        print(f"[gate] {job_id} finishing an earlier post (attempt {rec.get('attempts', 0) + 1})")
        return "publish"

    decision, messages = decision_for(job_id)
    print(f"[gate] {job_id} decision: {decision} (mode {MODE})")
    if decision == "approve":
        lock_buttons(messages, "✅ Approved · posting now")
        return "publish"
    if decision == "reject":
        mark_done(job_id, "rejected")
        lock_buttons(messages, "❌ Rejected")
        notify(f"⏭ Skipped (rejected): {title}")
        return "done"
    if MODE == "final":
        mark_done(job_id, "expired")
        notify(f"⏭ Skipped (not approved before the window closed): {title}")
        return "done"
    if MODE == "manual":
        notify(f"⏳ No Approve press found yet for: {title}\n(video id {job_id}). "
               f"Press ✅ Approve under that video, then run Publish again.")
    return "wait"


if __name__ == "__main__":
    action = decide()
    print(f"[gate] action = {action}")
    out = os.getenv("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"action={action}\n")
