"""Automated quality checks run on every video before it is sent for approval.

Blocking checks (a failure means the video is NOT auto-published and is clearly flagged in Telegram):
  format 1080x1920, duration 15-60s, audio present, loudness in range, no black cover frame,
  captions in sync with the voice, fact-check passed, at least one source link.
Advisory checks (shown, but don't block): primary/official source, keyword in title, hashtag count,
  similarity to recent topics, runtime estimate.
"""
import json
import subprocess

from rapidfuzz import fuzz

from . import media, trends


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,width,height,avg_frame_rate:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _first_frame_brightness(path):
    """Average brightness (0-255) of frame 0, which Instagram/Facebook use as the default cover."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1", "-vf", "scale=64:-1,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return sum(raw) / max(1, len(raw))


def run_checks(video, meta, caption_coverage, fact_report):
    checks = []

    def add(name, ok, detail, blocking=True):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "blocking": blocking})

    info = _probe(video)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    dur = float(info["format"]["duration"])
    add("Format 9:16 1080x1920", (v.get("width"), v.get("height")) == (1080, 1920), f"{v.get('width')}x{v.get('height')}")
    add("Duration 15-60s", 15 <= dur <= 60, f"{dur:.1f}s")
    add("Audio track", has_audio, "present" if has_audio else "missing")
    if has_audio:
        lufs, peak = media.loudness(video)
        add("Loudness -18 to -11 LUFS", -18 <= lufs <= -11, f"{lufs:.1f} LUFS, peak {peak:.1f} dBTP")
        add("No clipping", peak <= -0.5, f"true peak {peak:.1f} dBTP")
    bright = _first_frame_brightness(video)
    add("Cover frame not dark", bright >= 35, f"brightness {bright:.0f}/255")
    add("Captions in sync", caption_coverage >= 0.7, f"{caption_coverage:.0%} of words matched to speech")

    unverified = fact_report.get("unverified", [])
    add("Fact-check", fact_report.get("passed"),
        "all claims found in sources" if fact_report.get("passed") else f"{len(unverified)} unverified: " + "; ".join(unverified)[:300])
    add("Source links", len(meta.get("sources", [])) >= 1, f"{len(meta.get('sources', []))} sources")

    add("Official/primary source", bool(meta.get("primary_source")), meta.get("primary_source") or "news reports only", False)
    kw = (meta.get("primary_keyword") or "").lower()
    add("Keyword in title", kw and fuzz.partial_ratio(kw, meta["title"].lower()) >= 80, kw or "no keyword", False)
    n_tags = len(meta.get("hashtags", []))
    add("Hashtags 3-8", 3 <= n_tags <= 8, f"{n_tags} hashtags", False)
    sim = max([fuzz.token_set_ratio(meta["topic"].lower(), u["title"].lower()) for u in trends.load_used()] or [0])
    add("Different from recent posts", sim < 70, f"max similarity {sim:.0f}%", False)

    passed = all(c["ok"] for c in checks if c["blocking"])
    return {"passed": passed, "checks": checks}


def summary(report):
    head = "✅ Quality checks passed" if report["passed"] else "⚠️ Quality checks FAILED (won't auto-publish)"
    lines = [head]
    for c in report["checks"]:
        mark = "✅" if c["ok"] else ("❌" if c["blocking"] else "➖")
        lines.append(f"{mark} {c['name']}: {c['detail']}")
    return "\n".join(lines)
