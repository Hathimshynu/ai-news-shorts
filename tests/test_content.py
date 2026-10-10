"""Tests for trend scoring, fact-check verification, captions, QA, captions text, analytics and auto mode."""
import json
import shutil
import subprocess
from datetime import datetime, timezone

import pytest

from pipeline import analytics, config, gate, instagram, media, qa, script_gen, trends


def _it(title, kind, link="https://example.com/x", country="IN", engagement=0):
    return trends._item(title, link, kind, kind, published=datetime.now(timezone.utc), country=country,
                        engagement=engagement)


def test_official_source_outscores_plain_news():
    official = trends.cluster([_it("OpenAI launches GPT agent mode", "official", "https://openai.com/news/agent")])[0]
    news = trends.cluster([_it("Some phone gets update", "news")])[0]
    assert trends._score(official) > trends._score(news)
    assert trends.is_primary("https://openai.com/news/agent") and not trends.is_primary("https://example.com")


def test_duplicate_stories_merge_and_keep_most_credible_headline():
    c = trends.cluster([_it("OpenAI launches new agent mode in ChatGPT", "news"),
                        _it("OpenAI launches agent mode in ChatGPT for everyone", "official", "https://openai.com/a")])
    assert len(c) == 1 and len(c[0]["items"]) == 2
    assert c[0]["title"].endswith("for everyone")


def test_fact_check_rejects_quotes_not_in_source(monkeypatch):
    article = "Google said Gemini will be free for students in India until December 2026."
    reply = {"claims": [
        {"claim": "Gemini is free for Indian students", "verdict": "supported",
         "evidence": "Gemini will be free for students in India until December 2026", "status": "available"},
        {"claim": "Gemini is 50% faster", "verdict": "supported",
         "evidence": "Gemini is fifty percent faster than before", "status": "available"}]}
    monkeypatch.setattr(script_gen, "chat_json", lambda *a, **k: (reply, "test"))
    pkg = type("P", (), {"script": "x"})()
    rep = script_gen.fact_check(pkg, article)
    assert [c["verified"] for c in rep["claims"]] == [True, False]
    assert rep["passed"] is False and rep["unverified"] == ["Gemini is 50% faster"]


def test_pronunciation_only_changes_voice_text():
    pkg = script_gen.Package.model_validate({
        "hook": "h", "script": " ".join(["Nvidia"] + ["word"] * 99), "description": "d", "tags": [], "hashtags": [],
        "thumbnail_text": "t", "title_youtube": "t",
        "scenes": [{"text_overlay": "a", "pixabay_keywords": "b"}] * 5,
        "pronunciation": [{"term": "Nvidia", "say_as": "En-vidia"}], "category": "new ai model"})
    assert script_gen.tts_text(pkg).startswith("En-vidia") and pkg.script.startswith("Nvidia")
    assert pkg.category == "New AI Models"


def test_captions_keep_script_spelling_and_report_sync():
    heard = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3}
             for i, w in enumerate("google just launched gem in eye for students".split())]
    words, coverage = media.align_words("Google just launched Gemini for students.", heard)
    assert [w["word"] for w in words] == ["Google", "just", "launched", "Gemini", "for", "students."]
    assert 0.8 <= coverage < 1 and all(words[i]["start"] <= words[i + 1]["start"] for i in range(len(words) - 1))


def test_instagram_caption_has_question_cta_source_and_no_shorts_tag(monkeypatch):
    monkeypatch.setattr(config, "CHANNEL_HANDLE", "@tech_talk_hathim")
    cap = instagram.build_caption({"title": "T #shorts", "description": "d", "summary": "What happened.",
                                   "ig_opening": "Big news!", "ig_question": "Would you use it?",
                                   "hashtags": ["#shorts", "#ai"], "sources": ["https://www.livemint.com/a"]})
    assert cap.startswith("Big news!") and "Would you use it?" in cap and "@tech_talk_hathim" in cap
    assert "livemint.com" in cap and "#shorts" not in cap and len(cap) <= 2200


def test_engagement_rate():
    assert analytics.engagement_rate({"views": 1000, "likes": 50, "comments": 10, "shares": 5, "saves": 5}) == 7.0
    assert analytics.engagement_rate({"likes": 3}) is None


def test_auto_publish_posts_only_qa_passed_videos(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "CANDIDATES", tmp_path / "c"); monkeypatch.setattr(gate, "OUT", tmp_path / "out")
    monkeypatch.setattr(gate, "DONE", tmp_path / "p.json"); monkeypatch.setattr(gate, "AUTO", True)
    monkeypatch.setattr(gate, "MODE", "check")
    monkeypatch.setattr(gate, "tg", lambda m, **k: {"ok": True, "result": []})
    for rid, job, ok in (("1", "J-bad", False), ("2", "J-good", True)):
        (tmp_path / "c" / rid).mkdir(parents=True)
        (tmp_path / "c" / rid / "meta.json").write_text(json.dumps({"job_id": job, "title": job, "qa": {"passed": ok}}))
    assert gate.decide() == "publish"
    assert json.loads((tmp_path / "out" / "meta.json").read_text())["job_id"] == "J-good"
    gate.mark_done("J-good", "published")
    assert gate.decide() == "wait"     # QA-failed video is never auto-published


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_qa_flags_black_cover_and_wrong_size(tmp_path, monkeypatch):
    v = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=black:s=720x1280:d=20",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=20", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(v)], check=True)
    monkeypatch.setattr(trends, "load_used", lambda: [])
    rep = qa.run_checks(v, {"title": "t", "topic": "t", "sources": ["https://a.com"], "hashtags": ["#a"] * 4},
                        0.95, {"passed": True})
    failed = {c["name"] for c in rep["checks"] if not c["ok"]}
    assert not rep["passed"] and "Cover frame not dark" in failed and "Format 9:16 1080x1920" in failed


def test_cta_and_opinion_lines_do_not_fail_fact_check(monkeypatch):
    """The Sophos video: real facts verified, but the CTA and an opinion line made QA fail."""
    article = "Average response time for agent-handled cases fell 96%, from 38 minutes to 89 seconds."
    reply = {"claims": [
        {"claim": "Response time fell ninety-six percent, from thirty-eight minutes to eighty-nine seconds.",
         "type": "fact", "verdict": "supported",
         "evidence": "Average response time for agent-handled cases fell 96%, from 38 minutes to 89 seconds."},
        {"claim": "For businesses across Asia facing rising threats, automated defense is becoming essential.",
         "type": "opinion", "verdict": "unclear", "evidence": ""},
        {"claim": "Follow Tech Talk Hathim for daily AI updates.", "verdict": "unclear", "evidence": ""},
        {"claim": "Sophos now resolves 90 percent of cases.", "type": "opinion", "verdict": "unsupported"}]}
    monkeypatch.setattr(script_gen, "chat_json", lambda *a, **k: (reply, "test"))
    rep = script_gen.fact_check(type("P", (), {"script": "x", "cta": "Follow Tech Talk Hathim"})(), article)
    assert [c["type"] for c in rep["claims"]] == ["fact", "opinion", "cta", "fact"]
    assert rep["unverified"] == ["Sophos now resolves 90 percent of cases."]   # numbers can't hide as opinion
    reply["claims"].pop()
    assert script_gen.fact_check(type("P", (), {"script": "x", "cta": ""})(), article)["passed"] is True


def _pkg(**over):
    base = {"hook": "h", "script": " ".join(["word"] * 100), "scenes": [{"text_overlay": "a", "pixabay_keywords": "b"}] * 5,
            "title_youtube": "Sophos Uses OpenAI to Cut Response Time to 89s", "primary_keyword": "openai daybreak sophos",
            "related_keywords": ["ai cybersecurity india", "sophos mdr"], "description": "Sophos cut investigation time.",
            "tags": ["ai", "security"], "hashtags": ["#shorts", "#AI", "#Sophos"],
            "thumbnail_text": "sophos 96% faster threat response", "thumbnail_badge": "ai security news today"}
    base.update(over)
    return script_gen.Package.model_validate(base)


def test_seo_rules_catch_the_real_sophos_title():
    issues = script_gen.seo_issues(_pkg())
    assert any("primary keyword" in i and "title" in i for i in issues)        # keyword missing from title
    assert any("description" in i for i in issues)


def test_seo_fix_makes_every_upload_searchable():
    p = script_gen.fix_seo(_pkg())
    title = p.title_youtube
    assert title.endswith("#Shorts") and title.lower().count("#shorts") == 1
    assert "openai daybreak sophos" in title.lower()[:50] and len(title.replace(" #Shorts", "")) <= 65
    assert p.description.lower().startswith("openai daybreak sophos")
    assert p.tags[0] == "openai daybreak sophos" and "sophos mdr" in p.tags     # keyword + related first
    assert p.hashtags[-1] == "#Shorts" and len(p.hashtags) <= 6
    assert len(p.thumbnail_text.split()) <= 4 and p.thumbnail_badge == "AI SECURITY NEWS"


def test_good_package_passes_seo():
    p = _pkg(title_youtube="OpenAI Daybreak: Sophos Cuts Threat Response 96%",
             description="OpenAI Daybreak helped Sophos cut threat investigations from 38 minutes to 89 seconds.",
             thumbnail_text="Sophos 96% Faster")
    assert script_gen.seo_issues(p) == []


def test_cover_highlights_the_number():
    from pipeline import thumbnail
    assert thumbnail.highlight_index("SOPHOS 96% FASTER".split()) == 1
    assert thumbnail.highlight_index("GEMINI NOW FREE".split()) == 0
