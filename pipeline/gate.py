"""Cheap approval check that runs every 15 minutes during each posting window.
Standard library only, so it starts in seconds without installing anything.

Decides one action for the latest video and writes it to $GITHUB_OUTPUT:
  publish  - you pressed Approve -> the workflow installs packages and posts it
  done     - already posted / rejected / expired, or no video -> nothing to do
  wait     - no decision yet -> check again in 15 minutes

MODE (set by the workflow):
  check   - scheduled check inside the window: stay silent while waiting
  final   - last check of the window: if still not approved, tell you it was skipped
  manual  - you ran the workflow by hand: report status but never expire the video
"""
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
META = ROOT / "out" / "meta.json"
DONE = ROOT / "data" / "published.json"
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = str(os.getenv("TELEGRAM_CHAT_ID", ""))
MODE = os.getenv("MODE", "check")


def tg(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/{method}", data, timeout=30) as r:
        return json.loads(r.read())


def notify(text):
    try:
        tg("sendMessage", chat_id=CHAT, text=text[:4000])
    except Exception as e:  # noqa: BLE001
        print(f"[gate] notify failed: {e}")


def load_done():
    return json.loads(DONE.read_text()) if DONE.exists() else {}


def mark_done(job_id, status):
    done = load_done()
    done[job_id] = status
    done = dict(sorted(done.items())[-60:])  # keep the last ~30 days
    DONE.parent.mkdir(parents=True, exist_ok=True)
    DONE.write_text(json.dumps(done, indent=2) + "\n")


def decision_for(job_id):
    """Look at button presses without consuming them. Latest press wins."""
    res = tg("getUpdates", allowed_updates=json.dumps(["callback_query"]), limit=100)
    decision = None
    for u in res.get("result", []):
        cq = u.get("callback_query") or {}
        if str(cq.get("message", {}).get("chat", {}).get("id")) != CHAT:
            continue
        action, _, jid = (cq.get("data") or "").partition(":")
        if jid == job_id and action in ("approve", "reject"):
            decision = action
    return decision


def decide():
    if not META.exists():
        if MODE == "manual":
            notify("ℹ️ No video from the last 8 hours to publish.")
        return "done"
    meta = json.loads(META.read_text())
    job_id, title = meta["job_id"], meta["title"]

    if job_id in load_done():
        print(f"[gate] {job_id} already handled: {load_done()[job_id]}")
        return "done"

    decision = decision_for(job_id)
    print(f"[gate] {job_id} decision: {decision} (mode {MODE})")
    if decision == "approve":
        return "publish"
    if decision == "reject":
        mark_done(job_id, "rejected")
        notify(f"⏭ Skipped (rejected): {title}")
        return "done"
    if MODE == "final":
        mark_done(job_id, "expired")
        notify(f"⏭ Skipped (not approved before the window closed): {title}")
        return "done"
    if MODE == "manual":
        notify(f"⏳ Not approved yet: {title}\nPress Approve in Telegram; it will post within 15 minutes.")
    return "wait"


if __name__ == "__main__":
    action = decide()
    print(f"[gate] action = {action}")
    out = os.getenv("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"action={action}\n")
