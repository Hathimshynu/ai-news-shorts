"""Approval check that runs every 15 minutes during each posting window (and when run by hand).
Standard library only, so it starts in seconds without installing anything.

The workflow downloads every video made in the last 24 hours into candidates/<run_id>/.
This script reads your Telegram button presses and picks the video YOU approved (not just the
newest one), copies it into out/ and writes the action to $GITHUB_OUTPUT:
  publish  - an approved (or half-posted) video is ready in out/
  done     - nothing to post
  wait     - no decision yet -> check again in 15 minutes

MODE: check (scheduled, silent while waiting) | final (last check of a window: unanswered
videos are skipped) | manual (run by hand: posts immediately if approved, otherwise explains why not)

data/published.json keeps one record per video so nothing is ever posted twice:
  {"2026-10-09-evening-1512": {"status": "posting", "platforms": {"youtube": "https://..."}, "attempts": 1}}
"""
import json
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
META = OUT / "meta.json"
CANDIDATES = ROOT / "candidates"
DONE = ROOT / "data" / "published.json"
TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
CHAT = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
MODE = os.getenv("MODE", "check")
AUTO = (os.getenv("AUTO_PUBLISH") or "").strip().lower() == "true"  # post QA-passed videos without Approve
FINAL_STATES = ("published", "rejected", "expired", "failed")
MAX_ATTEMPTS = 3


def tg(method, **params):
    data = urllib.parse.urlencode(params).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/{method}", data, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read() or b"{}")
        except ValueError:
            return {"ok": False, "description": str(e)}


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
    done = dict(sorted(done.items())[-90:])
    DONE.parent.mkdir(parents=True, exist_ok=True)
    DONE.write_text(json.dumps(done, indent=2, ensure_ascii=False) + "\n")


def mark_done(job_id, status):
    rec = get(job_id)
    rec["status"] = status
    put(job_id, rec)


# ---------- videos ----------
def candidates():
    """[(job_id, folder, meta)] newest first. Falls back to out/ when there is no candidates/ folder."""
    found = []
    if CANDIDATES.exists():
        dirs = sorted((d for d in CANDIDATES.iterdir() if (d / "meta.json").exists()),
                      key=lambda d: int(d.name) if d.name.isdigit() else 0, reverse=True)
        for d in dirs:
            meta = json.loads((d / "meta.json").read_text())
            if meta["job_id"] not in [f[0] for f in found]:
                found.append((meta["job_id"], d, meta))
    elif META.exists():
        meta = json.loads(META.read_text())
        found.append((meta["job_id"], OUT, meta))
    return found


def select(folder):
    """Put the chosen video into out/ for pipeline.publish."""
    if Path(folder).resolve() != OUT.resolve():
        shutil.rmtree(OUT, ignore_errors=True)
        shutil.copytree(folder, OUT)


# ---------- Telegram buttons ----------
def _mine(cq):
    msg_chat = str(((cq.get("message") or {}).get("chat") or {}).get("id", "")).strip()
    from_id = str((cq.get("from") or {}).get("id", "")).strip()
    return CHAT in (msg_chat, from_id)


def presses():
    """{job_id: (decision, [(chat_id, message_id)])} from button presses, without consuming them."""
    res = tg("getUpdates", allowed_updates=json.dumps(["callback_query"]), limit=100)
    if not res.get("ok") and res.get("error_code") == 409:
        # A webhook is set on this bot, which blocks reading button presses. Remove it and retry.
        print(f"[gate] {res.get('description')} -> removing webhook")
        tg("deleteWebhook", drop_pending_updates="false")
        notify("ℹ️ Your Telegram bot had a webhook set, which blocks the Approve buttons. "
               "I removed it. If you set a webhook elsewhere (n8n, another tool), please remove it there.")
        res = tg("getUpdates", allowed_updates=json.dumps(["callback_query"]), limit=100)
    if not res.get("ok"):
        print(f"[gate] Telegram API error: {res}")
        return {}
    out = {}
    for u in res.get("result", []):
        cq = u.get("callback_query") or {}
        action, _, jid = (cq.get("data") or "").partition(":")
        mine = _mine(cq)
        print(f"[gate] press update={u.get('update_id')} data={cq.get('data')!r} mine={mine}")
        if not mine or action not in ("approve", "reject"):
            continue
        prev = out.get(jid, (None, []))
        msgs = prev[1]
        msg = cq.get("message") or {}
        if msg.get("message_id"):
            msgs = msgs + [(msg["chat"]["id"], msg["message_id"])]
        out[jid] = (action, msgs)  # latest press wins
    return out


def lock_buttons(messages, label):
    """Replace Approve/Reject with a status label so the video can't be approved twice."""
    markup = json.dumps({"inline_keyboard": [[{"text": label, "callback_data": "noop"}]]})
    for chat_id, message_id in set(messages):
        res = tg("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup=markup)
        if not res.get("ok") and "not modified" not in str(res.get("description", "")):
            print(f"[gate] could not update buttons: {res}")


# ---------- decision ----------
def decide():
    vids = candidates()
    open_vids = [(j, d, m) for j, d, m in vids if get(j)["status"] not in FINAL_STATES]
    print(f"[gate] videos: {[(j, get(j)['status']) for j, _, _ in vids]}")
    if not open_vids:
        if MODE == "manual":
            notify("ℹ️ Nothing to publish: no new video from the last 24 hours "
                   "(all are already posted, rejected or skipped).")
        return "done"

    # 1. Finish a post that was interrupted or partly failed
    for job_id, folder, meta in open_vids:
        rec = get(job_id)
        if rec["status"] == "posting":
            if rec.get("attempts", 0) >= MAX_ATTEMPTS:
                mark_done(job_id, "published" if rec["platforms"] else "failed")
                continue
            print(f"[gate] finishing earlier post of {job_id}")
            select(folder)
            return "publish"

    # 2. Your Approve / Reject presses
    decisions = presses()
    for job_id, folder, meta in open_vids:
        decision, msgs = decisions.get(job_id, (None, []))
        if decision == "reject":
            mark_done(job_id, "rejected")
            lock_buttons(msgs, "❌ Rejected")
            notify(f"⏭ Skipped (rejected): {meta['title']}")
    for job_id, folder, meta in open_vids:  # newest approved video first
        decision, msgs = decisions.get(job_id, (None, []))
        if get(job_id)["status"] in FINAL_STATES:
            continue  # rejected just now
        auto = AUTO and decision is None and (meta.get("qa") or {}).get("passed")
        if decision == "approve" or auto:
            if auto:
                print(f"[gate] auto-publish (QA passed): {job_id}")
            print(f"[gate] approved: {job_id} {meta['title']!r}")
            lock_buttons(msgs, "✅ Approved · posting now")
            select(folder)
            return "publish"

    waiting = [(j, m) for j, _, m in open_vids if get(j)["status"] not in FINAL_STATES]
    if MODE == "final":
        for job_id, meta in waiting:
            mark_done(job_id, "expired")
            notify(f"⏭ Skipped (not approved before the window closed): {meta['title']}")
        return "done"
    if MODE == "manual" and waiting:
        seen = ", ".join(f"{a}:{j}" for j, (a, _) in decisions.items()) or "none"
        lines = [f"• {m['title']}  (id {j})" for j, m in waiting]
        notify("⏳ No Approve press found for these videos:\n" + "\n".join(lines)
               + f"\n\nButton presses I can see (last 24h): {seen}"
               + "\nPress ✅ Approve under the video in Telegram, then run Publish again.")
    return "wait" if waiting else "done"


if __name__ == "__main__":
    action = decide()
    print(f"[gate] action = {action}")
    out = os.getenv("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"action={action}\n")
