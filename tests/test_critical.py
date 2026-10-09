"""Tests for the critical workflows: LLM fallback, approval detection, no double posting.
Run:  pip install pytest && python -m pytest -q"""
import json
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
