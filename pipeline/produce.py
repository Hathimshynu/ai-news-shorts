"""Workflow 1: research -> script -> assets -> render -> thumbnail -> send to Telegram for approval.
Run locally with:  python -m pipeline.produce"""
import json
import math
import random
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config, media, qa, script_gen, telegram_bot, thumbnail, trends

FPS = 30
TAIL_FRAMES = 20  # short pause after the voice ends


def _reset_dirs():
    for d in (config.OUT, config.JOB):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)


def _pick_music():
    music_dir = config.ROOT / "renderer" / "public" / "music"
    tracks = sorted(music_dir.glob("*.mp3")) if music_dir.exists() else []
    return f"music/{random.choice(tracks).name}" if tracks else None


def _scene_timeline(clips, scenes, total_frames):
    n = len(scenes)
    per = total_frames // n
    timeline = []
    for i, (clip, scene) in enumerate(zip(clips, scenes)):
        start = i * per
        dur = total_frames - start if i == n - 1 else per
        timeline.append({
            "file": clip["file"],
            "text": scene.text_overlay,
            "startFrame": start,
            "durationInFrames": dur,
            "clipFrames": max(1, int(clip["seconds"] * FPS) - 2),
        })
    return timeline


def _shrink_for_telegram(path, limit_mb=48):
    if path.stat().st_size <= limit_mb * 1024 * 1024:
        return path
    small = path.with_name("final_tg.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-c:v", "libx264", "-crf", "30",
                    "-preset", "veryfast", "-c:a", "aac", "-b:a", "96k", str(small)], check=True)
    return small


def run():
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    slot = "morning" if now.hour < 12 else "evening"
    publish_at, closes = ("8 AM", "11 AM") if slot == "morning" else ("7 PM", "10 PM")
    job_id = f"{now:%Y-%m-%d}-{slot}-{now:%H%M}"  # unique per video
    _reset_dirs()

    # 1. Trend discovery across all sources, then the editor picks (with category variety)
    clusters = trends.top_clusters(12)
    if not clusters:
        raise RuntimeError("No fresh stories found in any source")
    ranked = script_gen.pick_topic(clusters, avoid_categories=trends.recent_categories())

    # 2. Script package + fact-check (one automatic rewrite without the unverified claims)
    pkg = provider = chosen = article = facts = None
    for cluster in ranked[:3]:
        try:
            article, urls, primary = trends.article_text(cluster)
            pkg, provider = script_gen.write_package(cluster["title"], article)
            facts = script_gen.fact_check(pkg, article)
            if not facts["passed"] and facts["unverified"]:
                print(f"[produce] rewriting without unverified claims: {facts['unverified']}")
                pkg, provider = script_gen.write_package(cluster["title"], article, remove_claims=facts["unverified"])
                facts = script_gen.fact_check(pkg, article)
            chosen = {"title": cluster["title"], "urls": urls, "primary": primary, "score": cluster["score"],
                      "reason": cluster.get("reason", ""), "kinds": cluster.get("kinds", [])}
            break
        except Exception as e:  # noqa: BLE001
            print(f"[produce] story failed '{cluster['title']}': {e}")
    if not pkg:
        raise RuntimeError("Could not write a script for any of the top 3 stories")
    print(f"[produce] topic: {chosen['title']} [{pkg.category}] (LLM: {provider}) facts ok: {facts['passed']}")
    trends.save_backlog([c for c in ranked[1:8] if not trends.same_story(c["title"], chosen["title"])])
    _save_trends_snapshot(job_id, ranked, chosen)

    # 3. Voiceover (pronunciation fixes, loudness-normalised); rewrite shorter if too long
    raw, voice = config.JOB / "voice_raw.mp3", config.JOB / "voice.mp3"
    seconds = media.tts(script_gen.tts_text(pkg), raw)
    if seconds > config.MAX_AUDIO_SECONDS:
        print(f"[produce] audio {seconds:.1f}s too long, asking for a shorter script")
        pkg, provider = script_gen.write_package(chosen["title"], article, shorter=True,
                                                 remove_claims=facts["unverified"] or None)
        facts = script_gen.fact_check(pkg, article)
        seconds = media.tts(script_gen.tts_text(pkg), raw)
    seconds = media.normalize_audio(raw, voice)
    print(f"[produce] voice {seconds:.1f}s")

    # 4. Captions (script spelling + speech timings) and stock clips
    words, coverage = media.align_words(pkg.script, media.transcribe_words(voice))
    media.write_srt(words, config.OUT / "captions.srt")
    clips = media.download_clips(pkg.scenes, config.JOB / "clips")

    # 5. Render
    total_frames = math.ceil(seconds * FPS) + TAIL_FRAMES
    props = {
        "durationInFrames": total_frames,
        "hook": pkg.hook,
        "brand": config.CHANNEL_HANDLE,
        "voice": "job/voice.mp3",
        "music": _pick_music(),
        "scenes": _scene_timeline(clips, pkg.scenes, total_frames),
        "words": words,
    }
    props_path = config.OUT / "props.json"
    props_path.write_text(json.dumps(props, indent=2))
    final = config.OUT / "final.mp4"
    subprocess.run(["node", "render.mjs", str(props_path), str(final)], cwd=config.ROOT / "renderer", check=True)

    # 6. Thumbnail from a frame of the first clip
    frame = config.OUT / "frame.jpg"
    media.extract_frame(config.JOB / "clips" / "clip0.mp4", frame)
    thumbnail.render(pkg.thumbnail_text, frame, config.OUT)

    # 7. Metadata for the publish workflow
    meta = {
        "job_id": job_id,
        "slot": slot,
        "topic": chosen["title"],
        "selection_reason": chosen["reason"],
        "category": pkg.category,
        "llm": provider,
        "hooks": pkg.hooks,
        "hook": pkg.hook,
        "cta": pkg.cta,
        "titles": pkg.titles,
        "title": pkg.title_youtube,
        "primary_keyword": pkg.primary_keyword,
        "related_keywords": pkg.related_keywords,
        "description": script_gen.build_description(pkg, chosen["urls"]),
        "summary": pkg.description,
        "pinned_comment": pkg.pinned_comment,
        "ig_opening": pkg.ig_opening,
        "ig_question": pkg.ig_question,
        "tags": pkg.tags,
        "hashtags": pkg.hashtags,
        "script": pkg.script,
        "scenes": [s.model_dump() for s in pkg.scenes],
        "pronunciation": [p.model_dump() for p in pkg.pronunciation],
        "runtime_estimate": pkg.runtime_estimate,
        "sources": chosen["urls"],
        "primary_source": chosen["primary"],
        "facts": facts,
        "pixabay": [c["pixabay_page"] for c in clips],
        "seconds": round(seconds, 1),
        "caption_coverage": coverage,
    }

    # 8. Automated quality checks
    report = qa.run_checks(final, meta, coverage, facts)
    meta["qa"] = report
    (config.OUT / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    trends.save_used(chosen["title"], pkg.category)

    # 9. Telegram: video + approval buttons, then details
    tg_video = _shrink_for_telegram(final)
    if config.AUTO_PUBLISH and report["passed"]:
        when = f"🤖 Auto-publish ON: posts at {publish_at} IST unless you press Reject."
    else:
        when = (f"Tap Approve → the button changes within ~15 s to confirm. Posts at {publish_at} IST, "
                f"or within a minute if you approve later (until {closes}).")
    flag = "" if report["passed"] else "⚠️ QA FAILED, see report below\n"
    caption = (f"{flag}🎬 {meta['title']}\n🏷 {pkg.category}\n📰 {meta['topic']}\n"
               f"⏱ {meta['seconds']}s · 🤖 {provider}\n\n{when}")
    telegram_bot.send_video_for_approval(tg_video, caption, job_id)
    claims = "\n".join(f"{'✅' if c['verified'] else '❓'} {c['claim']} ({c['status']})" for c in facts["claims"][:8])
    telegram_bot.send_message(
        f"📝 Script:\n{pkg.script}\n\n🪝 Hook options:\n" + "\n".join(f"• {h}" for h in pkg.hooks)
        + f"\n\n🔎 Fact-check:\n{claims or 'no claims found'}\n\n{qa.summary(report)}"
        + f"\n\n📌 Pinned comment idea: {pkg.pinned_comment}\n\n🔗 Sources:\n"
        + ("\n".join(meta["sources"]) or "(RSS snippets only, check facts carefully)"))
    print("[produce] done")


def _save_trends_snapshot(job_id, ranked, chosen):
    snap = {"job_id": job_id, "chosen": chosen["title"], "reason": chosen.get("reason", ""),
            "candidates": [{"title": c["title"], "score": c["score"], "kinds": c.get("kinds", []),
                            "primary": c.get("primary", False),
                            "links": [i["link"] for i in c["items"][:3]]} for c in ranked[:12]]}
    (config.DATA / "trends_latest.json").write_text(json.dumps(snap, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        run()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        telegram_bot.notify(f"⚠️ AI News Shorts: producing today's video failed.\n{type(e).__name__}: {e}"[:1500])
        sys.exit(1)
