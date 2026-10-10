"""Approval gate: reads your Telegram Approve/Reject taps and decides what to post.
Standard library only (starts in seconds).

The workflow downloads every video made in the last 24 hours into candidates/<run_id>/.
This script finds the video YOU approved, copies it into out/ and says what to do:
  publish  - an approved (or half-posted) video is ready in out/
  done     - nothing left to post
  wait     - no decision yet, or approved but its posting time hasn't come

MODE
  watch  - (public repo) runs from when a video is made until it's posted or decided (latest 11 PM),
           checks Telegram every 10 seconds and confirms every tap immediately
  check  - one quick check (private repo / backup schedule)
  manual - run by hand: posts an approved video right now, otherwise explains why not

Posting times (IST): the day's first video (made before noon) posts at 8 PM, the second at 9 PM.
  ✅ Approve      -> posts at its time (immediately if that time has passed)
  ⚡ Post now     -> posts immediately
  ❌ Reject       -> not posted; you get 10 trending topics to make a replacement
Not decided by 11 PM -> skipped, with a "▶️ Post now" button if you still want it.

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
POLL_SECONDS = 10
WATCH_LIMIT = 5 * 3600 + 40 * 60   # GitHub jobs stop at 6 h; the next scheduled run takes over
REFRESH_SECONDS = 3 * 60           # look for newly made videos this often while watching
_answered = set()                  # callback ids already answered in this run
_labels = {}                       # (chat_id, message_id) -> label already shown
TOPIC_TAPS = []                    # [(job_id:index, callback_query)] from the "pick a topic" menu


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


POST_TIMES = [(20, 0), (21, 0)]  # IST: the day's first video posts at 8 PM, the second at 9 PM
DAY_START, DAY_END = 6, 23        # the watcher works 6 AM - 11 PM; unposted videos are skipped at 11 PM


def fmt(dt):
    return dt.strftime("%-I:%M %p")


def video_made(job_id):
    """job_id looks like 2026-10-10-morning-0615 (older ids have no time)."""
    parts = job_id.split("-")
    try:
        day = datetime(int(parts[0]), int(parts[1]), int(parts[2]), tzinfo=IST)
    except (ValueError, IndexError):
        return None
    hhmm = parts[4] if len(parts) > 4 and parts[4].isdigit() else ("0600" if parts[3] == "morning" else "1500")
    return day.replace(hour=int(hhmm[:2]), minute=int(hhmm[2:]))


def video_post_at(job_id):
    """Made before noon -> 8 PM. Made in the afternoon/evening -> 9 PM. Made after 10 PM -> 8 PM next day."""
    made = video_made(job_id)
    if not made:
        return None
    (h1, m1), (h2, m2) = POST_TIMES
    if made.hour < 12:
        return made.replace(hour=h1, minute=m1)
    if made.hour < 22:
        return made.replace(hour=h2, minute=m2)
    return (made + timedelta(days=1)).replace(hour=h1, minute=m1)


def video_window_end(job_id):
    at = video_post_at(job_id)
    return at.replace(hour=DAY_END, minute=0) if at else None


def window_over(job_id, now=None):
    end = video_window_end(job_id)
    return bool(end) and (now or now_ist()) >= end


def due(job_id, now=None):
    """Is it time to post this approved video? (Run by hand = always.)"""
    if MODE == "manual":
        return True
    at = video_post_at(job_id)
    return at is None or (now or now_ist()) >= at


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
    TOPIC_TAPS.clear()
    for u in res.get("result", []):
        cq = u.get("callback_query") or {}
        action, _, jid = (cq.get("data") or "").partition(":")
        mine = _mine(cq)
        if cq.get("id") not in _answered:
            print(f"[gate] tap update={u.get('update_id')} data={cq.get('data')!r} mine={mine}")
        if action == "noop" and cq.get("id"):
            answer(cq["id"], "Already decided 👍")
        if mine and action == "topic":
            TOPIC_TAPS.append((jid, cq))
            continue
        if not mine or action not in ("approve", "reject", "late", "now"):
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
    """Replace the buttons with a status label. With job_id (approved, waiting for its time),
    '⚡ Post now' and 'Cancel' stay available."""
    rows = [[{"text": label, "callback_data": "noop"}]]
    if job_id:
        rows.append([{"text": "⚡ Post now instead", "callback_data": f"now:{job_id}"},
                     {"text": "❌ Cancel", "callback_data": f"reject:{job_id}"}])
    markup = json.dumps({"inline_keyboard": rows})
    for chat_id, message_id in set(messages):
        if _labels.get((chat_id, message_id)) == (label, job_id):
            continue
        _labels[(chat_id, message_id)] = (label, job_id)
        res = tg("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup=markup)
        if not res.get("ok") and "not modified" not in str(res.get("description", "")):
            print(f"[gate] could not update buttons: {res}")


lock_buttons = set_buttons  # backwards-compatible name


# ---------- replace a rejected video with a topic you pick ----------
def _menu(folder):
    path = Path(folder) / "topics.json"
    return json.loads(path.read_text()) if path.exists() else []


def send_topic_menu(job_id, folder):
    rec = get(job_id)
    if rec.get("menu_sent"):
        return
    topics = _menu(folder)[:10]
    if not topics:
        notify("To make a new video now: Actions → Produce daily short → Run workflow.")
        return
    rows = [[{"text": f"{i + 1}. {t['title']}"[:60], "callback_data": f"topic:{job_id}:{i}"}]
            for i, t in enumerate(topics)]
    lines = [f"{i + 1}. {t['title']}" for i, t in enumerate(topics)]
    res = tg("sendMessage", chat_id=CHAT, reply_markup=json.dumps({"inline_keyboard": rows}),
             text=("🔥 Trending now. Tap one and I'll make a new video on it (~15 min):\n\n"
                   + "\n".join(lines))[:4000])
    rec["menu_sent"] = bool(res.get("ok"))
    put(job_id, rec)


def start_video(topic):
    """Start the produce workflow for this topic. Returns an error text, or None if it started."""
    if not os.getenv("GITHUB_ACTIONS"):
        return "not running on GitHub"
    payload = json.dumps(topic, ensure_ascii=False)
    r = subprocess.run(["gh", "workflow", "run", "produce.yml", "-f", f"topic_json={payload}"],
                       cwd=ROOT, capture_output=True, text=True)
    return None if r.returncode == 0 else (r.stderr or r.stdout).strip()[:300]


def handle_topic_taps(vids):
    folders = {j: d for j, d, _ in vids}
    for ref, cq in list(TOPIC_TAPS):
        job_id, _, idx = ref.rpartition(":")
        rec = get(job_id)
        msg = cq.get("message") or {}
        where = [(msg["chat"]["id"], msg["message_id"])] if msg.get("message_id") else []
        if rec.get("replacement"):
            answer(cq.get("id", ""), f"Already making: {rec['replacement']['title'][:120]}")
            continue
        topics = _menu(folders[job_id]) if job_id in folders else []
        if not idx.isdigit() or int(idx) >= len(topics):
            answer(cq.get("id", ""), "This menu has expired (older than 24 h). Run Produce from GitHub instead.")
            continue
        topic = topics[int(idx)]
        err = start_video(topic)
        if err:
            answer(cq.get("id", ""), "Couldn't start the new video, see the message below.")
            notify(f"⚠️ Couldn't start a video on \"{topic['title']}\": {err}\n"
                   "Fix: in publish.yml permissions, set actions: write. Or run Produce daily short by hand.")
            continue
        rec["replacement"] = {"title": topic["title"], "requested_at": datetime.now(timezone.utc).isoformat()}
        put(job_id, rec)
        _sh("bash scripts/save_record.sh")  # remember now, so a restart can't start it twice
        set_buttons(where, f"🎬 Making: {topic['title']}"[:60])
        answer(cq.get("id", ""), "🎬 Making your video. It arrives here in about 15 minutes.")
        notify(f"🎬 Making a new video on: {topic['title']}\nIt arrives here for approval in about 15 minutes.")


# ---------- videos whose window closed ----------
def offer_late(job_id, meta, was_approved=False):
    """Window closed: mark skipped and offer a one-tap 'Post now' (a fresh button, so an old
    Approve tap can never re-post something by accident)."""
    rec = get(job_id)
    if rec.get("late_offer"):
        return
    why = ("You approved it, but no check ran in time, so it wasn't posted."
           if was_approved else "It wasn't approved by 11 PM.")
    markup = {"inline_keyboard": [[{"text": "▶️ Post now", "callback_data": f"late:{job_id}"}]]}
    res = tg("sendMessage", chat_id=CHAT, reply_markup=json.dumps(markup),
             text=f"⏭ Skipped: {meta['title']}\n{why}\nTap ▶️ Post now to post it right away "
                  "(ignore this if it's already on your channels).")
    rec["status"] = "expired" if rec["status"] in ("new", "approved") else rec["status"]
    rec["late_offer"] = bool(res.get("ok"))
    put(job_id, rec)


# ---------- decision ----------
def decide(now=None):
    vids = candidates()
    decisions = presses() if vids else {}
    handle_topic_taps(vids)
    statuses = {j: get(j)["status"] for j, _, _ in vids}
    print(f"[gate] {now or now_ist():%H:%M} IST mode={MODE} videos: {statuses}")

    # "⚡ Post now" (or "▶️ Post now" on a skipped video) -> posts immediately, whatever the time
    for job_id, folder, meta in vids:
        decision, msgs, ids = decisions.get(job_id, (None, [], []))
        if (decision == "late" and statuses[job_id] == "expired") or \
                (decision == "now" and statuses[job_id] in ("new", "approved", "expired")):
            rec = get(job_id)
            rec.update(status="approved", late=True, approved_at=datetime.now(timezone.utc).isoformat())
            put(job_id, rec)
            statuses[job_id] = "approved"

    def approved(job_id):
        return statuses[job_id] == "approved" or (
            statuses[job_id] == "new" and decisions.get(job_id, (None,))[0] == "approve")

    # Window closed and never posted -> skipped, with a "Post now" button (any mode except manual)
    if MODE != "manual":
        for job_id, folder, meta in vids:
            rec = get(job_id)
            late = rec.get("late")
            if statuses[job_id] in ("new", "approved") and not late and window_over(job_id, now):
                offer_late(job_id, meta, was_approved=approved(job_id))
                statuses[job_id] = "expired"
            elif statuses[job_id] == "expired" and not rec.get("late_offer") and \
                    decisions.get(job_id, (None,))[0] == "approve":
                offer_late(job_id, meta, was_approved=True)  # approved after it had been skipped

    open_vids = [(j, d, m) for j, d, m in vids if statuses[j] not in FINAL_STATES]
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
            answer(ids, "❌ Rejected. Pick a topic for a new video below.")
            notify(f"⏭ Skipped (rejected): {meta['title']}")
            send_topic_menu(job_id, folder)

    # 3. Approvals (newest first) and auto-publish
    holding = retry_later
    for job_id, folder, meta in open_vids:
        if get(job_id)["status"] in ("rejected", "published", "failed", "posting"):
            continue  # "posting" = waiting to retry a failed platform (handled in step 1)
        decision, msgs, ids = decisions.get(job_id, (None, [], []))
        auto = AUTO and decision is None and statuses[job_id] == "new" and (meta.get("qa") or {}).get("passed")
        if not (approved(job_id) or auto):
            continue
        rec = get(job_id)
        if rec["status"] == "new":
            rec["status"] = "approved"
            rec["approved_at"] = datetime.now(timezone.utc).isoformat()
            put(job_id, rec)  # remembered even if Telegram forgets the tap
        if due(job_id, now) or rec.get("late"):
            print(f"[gate] {'auto-publish (QA passed)' if auto else 'approved'}: {job_id} {meta['title']!r}")
            set_buttons(msgs, "✅ Posting now")
            answer(ids, "✅ Posting now. Links arrive here in a few minutes.")
            select(folder)
            return "publish"
        at = video_post_at(job_id)
        when = f"{fmt(at)}" + ("" if at.date() == (now or now_ist()).date() else " tomorrow")
        set_buttons(msgs, f"✅ Approved · posts at {when}", job_id)
        answer(ids, f"✅ Approved! It will post at {when}. Tap ⚡ Post now to post immediately.")
        holding = True

    waiting = [(j, m) for j, _, m in open_vids if get(j)["status"] in ("new",)]
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


def _replacement_coming():
    """A topic was picked after a Reject less than 45 min ago: keep watching for that video."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=45)
    for rec in load_all().values():
        rep = rec.get("replacement") if isinstance(rec, dict) else None
        if rep and datetime.fromisoformat(rep["requested_at"]) > cutoff:
            return True
    return False


def watch():
    """Run from when a video is made until it's posted or decided (latest 11 PM); taps answered in ~10 s."""
    start = time.time()
    now = now_ist()
    if not (DAY_START <= now.hour < DAY_END):
        print("[gate] night time: one check, then stop")
        return decide()
    day_end = now.replace(hour=DAY_END, minute=0, second=0, microsecond=0)
    print(f"[gate] watching until everything is posted or decided (latest {fmt(day_end)} IST)")
    last_refresh = time.time()
    while True:
        try:
            action = decide()
        except Exception as e:  # noqa: BLE001 - keep watching
            print(f"[gate] check failed: {type(e).__name__}: {e}")
            action = "wait"
        if action == "publish":
            if _sh(f"{sys.executable} -m pipeline.publish") != 0:
                print("[gate] publish exited with an error (details were sent to Telegram)")
            _sh("bash scripts/save_record.sh")
            continue  # another video may be due
        if now_ist() >= day_end:
            _sh("bash scripts/save_record.sh")
            print("[gate] 11 PM: stopping (unposted videos were offered with a Post now button)")
            return "done"
        open_count = sum(1 for j, _, _ in candidates() if get(j)["status"] not in FINAL_STATES)
        if open_count == 0 and not _replacement_coming():
            _sh("bash scripts/save_record.sh")
            print("[gate] nothing waiting: stopping (the next video starts a new watcher)")
            return "done"
        if time.time() - start > WATCH_LIMIT:
            print("[gate] GitHub's 6-hour limit is near: starting a fresh watcher to continue")
            _sh("bash scripts/save_record.sh")
            if os.getenv("GITHUB_ACTIONS"):
                _sh("gh workflow run publish.yml -f mode=watch")
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
