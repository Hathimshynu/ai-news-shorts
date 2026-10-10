"""Tests for the critical workflows: LLM fallback, approval detection, no double posting.
Run:  pip install pytest && python -m pytest -q"""
import json
from datetime import datetime
from types import SimpleNamespace as NS

import pytest

from pipeline import config, gate, llm, publish


# ---------------------------------------------------------------- LLM router
class FakeErr(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.status_code = code


@pytest.fixture
def router(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g")
    monkeypatch.setattr(config, "GROQ_API_KEY", "bad")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "o")
    llm._models_cache.clear(); llm._dead_providers.clear(); llm._bad_models.clear()
    llm._models_cache.update({
        "gemini": ["gemini-old", "gemini-busy", "gemini-ok"],
        "groq": ["llama-3.3-70b-versatile", "other"],
        "openrouter": ["free-a"],
    })
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    calls = []
    return calls


def fake_ask(behaviour, calls):
    def ask(client, model, system, user, temperature):
        calls.append(model)
        b = behaviour.get(model, 200)
        if b != 200:
            raise FakeErr(b)
        return {"ok": True}
    return ask


def test_busy_and_retired_models_fall_back(router, monkeypatch):
    monkeypatch.setattr(llm, "_ask", fake_ask({"gemini-old": 404, "gemini-busy": 503}, router))
    data, used = llm.chat_json("s", "u")
    assert used == "gemini:gemini-ok" and data == {"ok": True}
    assert ("gemini", "gemini-old") in llm._bad_models          # retired model skipped from now on


def test_bad_key_skips_provider_and_next_provider_answers(router, monkeypatch):
    beh = {"gemini-old": 503, "gemini-busy": 503, "gemini-ok": 503, "llama-3.3-70b-versatile": 401}
    monkeypatch.setattr(llm, "_ask", fake_ask(beh, router))
    _, used = llm.chat_json("s", "u")
    assert used == "openrouter:free-a"
    assert "groq" in llm._dead_providers and "other" not in router   # no more calls with a bad key


def test_everything_down_retries_rounds_then_explains(router, monkeypatch):
    beh = {m: 503 for m in ["gemini-old", "gemini-busy", "gemini-ok", "free-a"]}
    beh["llama-3.3-70b-versatile"] = 401
    monkeypatch.setattr(llm, "_ask", fake_ask(beh, router))
    with pytest.raises(RuntimeError) as e:
        llm.chat_json("s", "u")
    assert "Fix these keys" in str(e.value) and "groq" in str(e.value)
    assert router.count("free-a") == llm.ROUNDS


# ---------------------------------------------------------------- approval gate
@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "META", tmp_path / "meta.json")
    monkeypatch.setattr(gate, "OUT", tmp_path)
    monkeypatch.setattr(gate, "CANDIDATES", tmp_path / "no-candidates")
    monkeypatch.setattr(gate, "DONE", tmp_path / "published.json")
    monkeypatch.setattr(config, "OUT", tmp_path)
    (tmp_path / "meta.json").write_text(json.dumps({"job_id": "J1", "title": "T", "description": "d",
                                                    "tags": [], "hashtags": []}))
    (tmp_path / "final.mp4").write_bytes(b"x")
    monkeypatch.setattr(gate, "CHAT", "42")
    return tmp_path


def telegram(presses, sent):
    def tg(method, **k):
        sent.append(method)
        if method == "getUpdates":
            return {"ok": True, "result": [
                {"update_id": i, "callback_query": {"data": d, "from": {"id": frm},
                                                    "message": {"message_id": 7, "chat": {"id": chat}}}}
                for i, (d, frm, chat) in enumerate(presses)]}
        return {"ok": True}
    return tg


def test_approve_found_and_buttons_locked(tmp_state, monkeypatch):
    sent = []
    monkeypatch.setattr(gate, "tg", telegram([("approve:J1", 42, 42)], sent))
    assert gate.decide() == "publish"
    assert "editMessageReplyMarkup" in sent          # buttons replaced -> can't approve twice


def test_approve_matched_by_sender_even_if_chat_differs(tmp_state, monkeypatch):
    monkeypatch.setattr(gate, "tg", telegram([("approve:J1", 42, -100999)], []))
    assert gate.decide() == "publish"


def test_strangers_and_other_videos_ignored(tmp_state, monkeypatch):
    monkeypatch.setattr(gate, "tg", telegram([("approve:J1", 5, 5), ("approve:J0", 42, 42)], []))
    assert gate.decide() == "wait"


# ---------------------------------------------------------------- no double posting
def test_publish_each_platform_once_and_retry_only_failed(tmp_state, monkeypatch):
    monkeypatch.setattr(publish.telegram_bot, "notify", lambda t: None)
    monkeypatch.setattr(publish.instagram, "enabled", lambda: True)
    monkeypatch.setattr(publish.facebook, "enabled", lambda: False)
    posted = []
    monkeypatch.setattr(publish, "_youtube", lambda m: posted.append("yt") or ("https://yt/1", "Y1"))
    state = {"ig_fail": True}

    def ig(meta):
        posted.append("ig")
        if state["ig_fail"]:
            raise RuntimeError("instagram down")
        return "https://ig/1", "I1"
    monkeypatch.setattr(publish, "_instagram", ig)

    publish.run()                                     # YouTube ok, Instagram fails
    rec = gate.get("J1")
    assert rec["status"] == "posting" and "youtube" in rec["platforms"]

    monkeypatch.setattr(gate, "tg", telegram([("approve:J1", 42, 42)], []))
    assert gate.decide() == "publish"                 # next check resumes the post
    state["ig_fail"] = False
    publish.run()
    assert posted == ["yt", "ig", "ig"]               # YouTube was NOT posted again
    assert gate.get("J1")["status"] == "published"

    assert gate.decide() == "done"                    # pressing Approve again changes nothing
    publish.run()
    assert posted == ["yt", "ig", "ig"]


# ---------------------------------------------------------------- the bug you hit
def test_approving_an_older_video_posts_that_video(tmp_path, monkeypatch):
    """Two videos on the same topic; Approve pressed on the OLDER one -> that one is posted."""
    out, cand = tmp_path / "out", tmp_path / "candidates"
    monkeypatch.setattr(gate, "OUT", out); monkeypatch.setattr(gate, "META", out / "meta.json")
    monkeypatch.setattr(gate, "CANDIDATES", cand); monkeypatch.setattr(gate, "DONE", tmp_path / "p.json")
    monkeypatch.setattr(gate, "CHAT", "42"); monkeypatch.setattr(gate, "MODE", "manual")
    for run_id, job in (("100", "2026-10-09-morning-1130"), ("200", "2026-10-09-evening-1310")):
        (cand / run_id).mkdir(parents=True)
        (cand / run_id / "meta.json").write_text(json.dumps({"job_id": job, "title": "UPI MDR " + job}))
    monkeypatch.setattr(gate, "tg", telegram([("approve:2026-10-09-morning-1130", 42, 42)], []))
    assert gate.decide() == "publish"
    assert json.loads((out / "meta.json").read_text())["job_id"] == "2026-10-09-morning-1130"


def test_webhook_blocking_updates_is_removed(tmp_state, monkeypatch):
    sent, state = [], {"hook": True}

    def tg(method, **k):
        sent.append(method)
        if method == "getUpdates" and state["hook"]:
            return {"ok": False, "error_code": 409, "description": "Conflict: webhook is active"}
        if method == "deleteWebhook":
            state["hook"] = False
        if method == "getUpdates":
            return {"ok": True, "result": [{"update_id": 1, "callback_query": {
                "data": "approve:J1", "from": {"id": 42}, "message": {"message_id": 7, "chat": {"id": 42}}}}]}
        return {"ok": True}
    monkeypatch.setattr(gate, "tg", tg)
    assert gate.decide() == "publish" and "deleteWebhook" in sent


# ---------------------------------------------------------------- live watcher behaviour
def test_early_approval_is_confirmed_and_held_until_window(tmp_state, monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(gate, "in_window", lambda now=None: False)
    monkeypatch.setattr(gate, "MODE", "watch")
    calls = []

    def tg(method, **k):
        calls.append((method, k))
        if method == "getUpdates":
            return {"ok": True, "result": [{"update_id": 1, "callback_query": {
                "id": "cb1", "data": "approve:J1", "from": {"id": 42},
                "message": {"message_id": 7, "chat": {"id": 42}}}}]}
        return {"ok": True}
    monkeypatch.setattr(gate, "tg", tg)
    six_am = datetime(2026, 10, 9, 6, 30, tzinfo=gate.IST)
    assert gate.decide(six_am) == "wait"
    toast = [k["text"] for m, k in calls if m == "answerCallbackQuery"]
    assert toast and "8:00 AM" in toast[0]                          # you see it registered
    assert gate.get("J1")["status"] == "approved"                   # remembered even if Telegram forgets
    labels = [json.loads(k["reply_markup"]) for m, k in calls if m == "editMessageReplyMarkup"]
    assert "Cancel" in labels[0]["inline_keyboard"][1][0]["text"]  # can still change your mind

    monkeypatch.setattr(gate, "in_window", lambda now=None: True)  # 8:00 AM
    monkeypatch.setattr(gate, "tg", telegram([], []))               # tap no longer visible
    assert gate.decide() == "publish"


def test_cancel_after_approve(tmp_state, monkeypatch):
    monkeypatch.setattr(gate, "in_window", lambda now=None: False)
    monkeypatch.setattr(gate, "tg", telegram([("approve:J1", 42, 42), ("reject:J1", 42, 42)], []))
    assert gate.decide() == "done" and gate.get("J1")["status"] == "rejected"


def _two_videos(tmp_path, monkeypatch, jobs):
    cand = tmp_path / "candidates"
    monkeypatch.setattr(gate, "CANDIDATES", cand); monkeypatch.setattr(gate, "OUT", tmp_path / "out")
    monkeypatch.setattr(gate, "DONE", tmp_path / "p.json"); monkeypatch.setattr(gate, "CHAT", "42")
    for n, job in enumerate(jobs):
        (cand / str(n + 1)).mkdir(parents=True)
        (cand / str(n + 1) / "meta.json").write_text(json.dumps({"job_id": job, "title": "T " + job}))


def test_window_end_follows_when_the_video_was_made():
    ist = gate.IST
    assert gate.video_window_end("2026-10-10-morning-0615") == datetime(2026, 10, 10, 11, tzinfo=ist)
    assert gate.video_window_end("2026-10-10-morning-1146") == datetime(2026, 10, 10, 22, tzinfo=ist)  # GitHub ran it late
    assert gate.video_window_end("2026-10-09-evening-2155") == datetime(2026, 10, 10, 11, tzinfo=ist)  # made after 10 PM
    assert gate.video_window_end("2026-10-09-evening") == datetime(2026, 10, 9, 22, tzinfo=ist)


def test_late_check_never_skips_a_fresh_video(tmp_path, monkeypatch):
    """A delayed run at 6:20 AM must only skip YESTERDAY's video, not this morning's."""
    _two_videos(tmp_path, monkeypatch, ["2026-10-09-evening-1530", "2026-10-10-morning-0615"])
    sent = []
    monkeypatch.setattr(gate, "tg", telegram([], sent)); monkeypatch.setattr(gate, "MODE", "check")
    gate.decide(datetime(2026, 10, 10, 6, 20, tzinfo=gate.IST))
    assert gate.get("2026-10-09-evening-1530")["status"] == "expired"
    assert gate.get("2026-10-10-morning-0615")["status"] == "new"


def test_old_approve_tap_on_skipped_video_never_reposts_but_post_now_does(tmp_path, monkeypatch):
    """Records were lost after a video was posted; its old Approve tap must not post it again."""
    _two_videos(tmp_path, monkeypatch, ["2026-10-09-evening-1938"])
    gate.put("2026-10-09-evening-1938", {"status": "expired", "platforms": {}})
    sent = []
    monkeypatch.setattr(gate, "MODE", "watch")
    monkeypatch.setattr(gate, "tg", telegram([("approve:2026-10-09-evening-1938", 42, 42)], sent))
    assert gate.decide() == "done"
    assert sent.count("sendMessage") == 1                      # one "Post now" offer, not a post
    assert gate.decide() == "done" and sent.count("sendMessage") == 1   # offered once
    monkeypatch.setattr(gate, "tg", telegram([("approve:2026-10-09-evening-1938", 42, 42),
                                              ("late:2026-10-09-evening-1938", 42, 42)], []))
    monkeypatch.setattr(gate, "in_window", lambda now=None: False)
    assert gate.decide() == "publish"                         # Post now = right away, even outside a window


def test_approved_but_window_passed_is_offered_not_lost(tmp_path, monkeypatch):
    _two_videos(tmp_path, monkeypatch, ["2026-10-09-evening-2155"])
    sent = []
    monkeypatch.setattr(gate, "MODE", "watch")
    monkeypatch.setattr(gate, "tg", telegram([("approve:2026-10-09-evening-2155", 42, 42)], sent))
    gate.decide(datetime(2026, 10, 10, 12, 0, tzinfo=gate.IST))
    assert gate.get("2026-10-09-evening-2155")["status"] == "expired" and "sendMessage" in sent


def test_failed_platform_not_retried_immediately_in_watch_mode(tmp_state, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(gate, "MODE", "watch")
    gate.put("J1", {"status": "posting", "attempts": 1, "platforms": {"youtube": "x"},
                    "last_try": datetime.now(timezone.utc).isoformat()})
    monkeypatch.setattr(gate, "tg", telegram([("approve:J1", 42, 42)], []))
    assert gate.decide() == "wait"


# ---------------------------------------------------------------- reject -> pick a new topic
def test_reject_offers_topics_and_tap_starts_one_video(tmp_state, monkeypatch):
    (tmp_state / "topics.json").write_text(json.dumps(
        [{"title": f"Story {i}", "items": [{"title": f"Story {i}", "link": f"https://a.com/{i}"}]} for i in range(12)]))
    sent, started = [], []
    taps = [("reject:J1", 42, 42)]

    def tg(method, **k):
        sent.append((method, k))
        if method == "getUpdates":
            return {"ok": True, "result": [{"update_id": n, "callback_query": {
                "id": f"cb{n}", "data": d, "from": {"id": 42}, "message": {"message_id": 7 + n, "chat": {"id": 42}}}}
                for n, d in enumerate(t[0] for t in taps)]}
        return {"ok": True}
    monkeypatch.setattr(gate, "tg", tg)
    monkeypatch.setattr(gate, "start_video", lambda topic: started.append(topic["title"]))
    assert gate.decide() == "done"
    menu = [json.loads(k["reply_markup"]) for m, k in sent if m == "sendMessage" and "reply_markup" in k]
    assert len(menu) == 1 and len(menu[0]["inline_keyboard"]) == 10      # top 10 offered once
    assert menu[0]["inline_keyboard"][2][0]["callback_data"] == "topic:J1:2"

    taps.append(("topic:J1:2", 42, 42))
    gate.decide()
    taps.append(("topic:J1:5", 42, 42))                                   # second tap: ignored
    gate.decide()
    assert started == ["Story 2"]
    assert gate.get("J1")["replacement"]["title"] == "Story 2"
    assert sum(1 for m, k in sent if m == "sendMessage" and "reply_markup" in k) == 1   # no duplicate menu
