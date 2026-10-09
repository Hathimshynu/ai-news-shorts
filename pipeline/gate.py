"""Approval gate: reads your Telegram Approve/Reject taps and decides what to post.
Standard library only (starts in seconds).

The workflow downloads every video made in the last 24 hours into candidates/<run_id>/.
This script finds the video YOU approved, copies it into out/ and says what to do:
  publish  - an approved (or half-posted) video is ready in out/
  done     - nothing left to post
  wait     - no decision yet, or approved but the posting window hasn't opened

MODE
  watch  - (public repo) stays running through the posting window, checks Telegram every
           10 seconds, confirms every tap immediately and posts within a minute
  check  - one quick check (private repo / backup schedule)
  final  - last check of a window: unanswered videos are marked skipped
  manual - run by hand: posts an approved video right now, otherwise explains why not

Posting windows (IST): 8-11 AM and 7-10 PM. Approve before the window -> posts when it opens.
Approve during the window -> posts within a minute. Approve a skipped video later -> posts in
the next window (or immediately if you run Publish by hand).

data/published.json keeps one record per video so nothing is ever posted twice.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
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
RETRY_AFTER = 10 * 60          # wait before retrying a platform that failed
IST = timezone(timedelta(hours=5, minutes=30))
WINDOWS = [(8, 11), (19, 22)]  # posting windows, IST hours
POLL_SECONDS = 10
WATCH_LIMIT = 5 * 3600 + 40 * 60   # GitHub jobs stop at 6 h; the next scheduled run takes over
REFRESH_SECONDS = 5 * 60           # look for newly made videos this often while watching
_answered = set()                  # callback ids already answered in this run
_labels = {}                       # (chat_id, message_id) -> label already shown


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
    except Exception as e:  # noqa: BLE001 - network hiccup: treat as "no answer", try again later
        return {"ok": False, "description": str(e)}


def notify(text):
    try:
        tg("sendMessage", chat_id=CHAT, text=text[:4000])
    except Exception as e:  # noqa: BLE001
        print(f"[gate] notify failed: {e}")


# ---------- time windows ----------
def now_ist():
    return datetime.now(IST)


def window(now=None):
    """(start, end) of the current or next posting window today, or None after the last one."""
    now = now or now_ist()
    for s, e in WINDOWS:
        end = now.replace(hour=e, minute=0, second=0, microsecond=0)
        if now < end:
            return now.replace(hour=s, minute=0, second=0, microsecond=0), end
    return None


def in_window(now=None):
    w = window(now)
    return bool(w) and w[0] <= (now or now_ist()) < w[1]


def fmt(dt):
    return dt.strftime("%-I:%M %p")


def can_post_now(now=None):
    return MODE in ("manual", "final") or in_window(now)


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
    """{job_id: (decision, [(chat_id, message_id)], [callback_ids])} from button taps.
    Taps are read without being consumed, so every later check still sees them (Telegram keeps 24 h)."""
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
        if cq.get("id") not in _answered:
            print(f"[gate] tap update={u.get('update_id')} data={cq.get('data')!r} mine={mine}")
        if action == "noop" and cq.get("id"):
            answer(cq["id"], "Already decided 👍")
        if not mine or action not in ("approve", "reject"):
            continue
        _, msgs, ids = out.get(jid, (None, [], []))
        msg = cq.get("message") or {}
        if msg.get("message_id"):
            msgs = msgs + [(msg["chat"]["id"], msg["message_id"])]
        if cq.get("id"):
            ids = ids + [cq["id"]]
        out[jid] = (action, msgs, ids)  # latest tap wins
    return out


def answer(callback_ids, text):
    """Show a small pop-up on your phone confirming the tap (only possible for recent taps)."""
    for cid in [callback_ids] if isinstance(callback_ids, str) else callback_ids:
        if cid in _answered:
            continue
        _answered.add(cid)
        tg("answerCallbackQuery", callback_query_id=cid, text=text[:190])


def set_buttons(messages, label, job_id=None):
    """Replace Approve/Reject with a status label. With job_id, a Cancel button stays available."""
    rows = [[{"text": label, "callback_data": "noop"}]]
    if job_id:
        rows.append([{"text": "❌ Cancel (don't post)", "callback_data": f"reject:{job_id}"}])
    markup = json.dumps({"inline_keyboard": rows})
    for chat_id, message_id in set(messages):
        if _labels.get((chat_id, message_id)) == (label, job_id):
            continue
        _labels[(chat_id, message_id)] = (label, job_id)
        res = tg("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup=markup)
        if not res.get("ok") and "not modified" not in str(res.get("description", "")):
            print(f"[gate] could not update buttons: {res}")


lock_buttons = set_buttons  # backwards-compatible name


# ---------- decision ----------
def decide(now=None):
    vids = candidates()
    decisions = presses() if vids else {}
    statuses = {j: get(j)["status"] for j, _, _ in vids}
    print(f"[gate] {now or now_ist():%H:%M} IST mode={MODE} videos: {statuses}")

    def approved(job_id):
        return decisions.get(job_id, (None,))[0] == "approve" or statuses[job_id] == "approved"

    # Open = not finished yet. A skipped (expired) video re-opens when you approve it later.
    open_vids = [(j, d, m) for j, d, m in vids
                 if statuses[j] not in FINAL_STATES or (statuses[j] == "expired" and approved(j)
                                                        and decisions.get(j, (None,))[0] != "reject")]
    if not open_vids:
        if MODE == "manual":
            notify("ℹ️ Nothing to publish: no new video from the last 24 hours "
                   "(all are already posted, rejected or skipped).")
        return "done"

    # 1. Finish a post that was interrupted or partly failed
    retry_later = False
    for job_id, folder, meta in open_vids:
        rec = get(job_id)
        if rec["status"] == "posting":
            if rec.get("attempts", 0) >= MAX_ATTEMPTS:
                mark_done(job_id, "published" if rec["platforms"] else "failed")
                continue
            last = rec.get("last_try")
            if MODE == "watch" and last and (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < RETRY_AFTER:
                retry_later = True
                continue
            print(f"[gate] finishing earlier post of {job_id}")
            select(folder)
            return "publish"

    # 2. Rejects (also "Cancel" after approving)
    for job_id, folder, meta in open_vids:
        decision, msgs, ids = decisions.get(job_id, (None, [], []))
        if decision == "reject":
            mark_done(job_id, "rejected")
            set_buttons(msgs, "❌ Rejected · won't be posted")
            answer(ids, "❌ Rejected. This video won't be posted.")
            notify(f"⏭ Skipped (rejected): {meta['title']}")

    # 3. Approvals (newest first) and auto-publish
    w = window(now)
    holding = retry_later
    for job_id, folder, meta in open_vids:
        if get(job_id)["status"] in ("rejected", "published", "failed", "posting"):
            continue  # "posting" = waiting to retry a failed platform (handled in step 1)
        decision, msgs, ids = decisions.get(job_id, (None, [], []))
        auto = AUTO and decision is None and statuses[job_id] == "new" and (meta.get("qa") or {}).get("passed")
        if not (approved(job_id) or auto):
            continue
        rec = get(job_id)
        if rec["status"] in ("new", "expired"):
            rec["status"] = "approved"
            rec["approved_at"] = datetime.now(timezone.utc).isoformat()
            put(job_id, rec)  # remembered even if Telegram forgets the tap
        if can_post_now(now):
            print(f"[gate] {'auto-publish (QA passed)' if auto else 'approved'}: {job_id} {meta['title']!r}")
            set_buttons(msgs, "✅ Approved · posting now")
            answer(ids, "✅ Approved! Posting now. Links arrive here in a few minutes.")
            select(folder)
            return "publish"
        when = f"{fmt(w[0])} IST" if w else "8:00 AM IST tomorrow"
        set_buttons(msgs, f"✅ Approved · posts at {when}", job_id)
        answer(ids, f"✅ Approved! It will post at {when}.")
        holding = True

    waiting = [(j, m) for j, _, m in open_vids if get(j)["status"] in ("new",)]
    if MODE == "final":
        for job_id, meta in waiting:
            mark_done(job_id, "expired")
            notify(f"⏭ Skipped (not approved before the window closed): {meta['title']}\n"
                   "Still want it? Tap ✅ Approve under the video: it posts in the next window "
                   "(or run Publish by hand to post right away).")
        return "wait" if holding else "done"
    if MODE == "manual" and waiting:
        seen = ", ".join(f"{a}:{j}" for j, (a, _, _) in decisions.items()) or "none"
        lines = [f"• {m['title']}  (id {j})" for j, m in waiting]
        notify("⏳ No Approve tap found for these videos:\n" + "\n".join(lines)
               + f"\n\nTaps I can see (last 24h): {seen}"
               + "\nTap ✅ Approve under the video in Telegram, then run Publish again.")
    return "wait" if (waiting or holding) else "done"


# ---------- watch mode ----------
def _sh(cmd):
    print(f"[gate] $ {cmd}")
    return subprocess.run(cmd, shell=True, cwd=ROOT).returncode


def _set_mode(mode):
    global MODE
    MODE = mode


def watch():
    """Stay running through the posting window; react to taps within ~10 seconds."""
    start = time.time()
    w = window()
    if not w:
        print("[gate] after the last window today: one final check")
        return decide()
    print(f"[gate] watching until {fmt(w[1])} IST")
    last_refresh = time.time()
    while True:
        try:
            action = decide()
        except Exception as e:  # noqa: BLE001 - keep watching; report once
            print(f"[gate] check failed: {type(e).__name__}: {e}")
            action = "wait"
        if action == "publish":
            if _sh(f"{sys.executable} -m pipeline.publish") != 0:
                print("[gate] publish exited with an error (details were sent to Telegram)")
            _sh("bash scripts/save_record.sh")
            continue  # another approved video may be waiting
        if MODE == "final":
            _sh("bash scripts/save_record.sh")
            print("[gate] window closed")
            return "done"
        if now_ist() >= w[1]:
            _set_mode("final")  # one last pass: post anything approved, mark the rest skipped
            continue
        if time.time() - start > WATCH_LIMIT:
            print("[gate] time limit reached; the next scheduled run continues")
            return "done"
        if time.time() - last_refresh > REFRESH_SECONDS and os.getenv("GITHUB_ACTIONS"):
            _sh("bash scripts/fetch_videos.sh")  # pick up a video produced while watching
            last_refresh = time.time()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    action = watch() if MODE == "watch" else decide()
    if MODE == "watch":
        action = "done"  # watch mode already posted everything itself
    print(f"[gate] action = {action}")
    out = os.getenv("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"action={action}\n")
