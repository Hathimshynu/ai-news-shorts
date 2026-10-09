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

from . import config, media, script_gen, telegram_bot, thumbnail, trends

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
    job_id = f"{now:%Y-%m-%d}-{slot}"
    _reset_dirs()

    # 1. Trends
    clusters = trends.top_clusters(10)
    if not clusters:
        raise RuntimeError("No fresh stories found in Google News feeds")
    ranked = script_gen.pick_topic(clusters)

    # 2. Script package (try the next story if the first can't be scripted)
    pkg = provider = chosen = None
    for cluster in ranked[:3]:
        try:
            article, urls = trends.article_text(cluster)
            pkg, provider = script_gen.write_package(cluster["title"], article)
            chosen = {"title": cluster["title"], "urls": urls, "score": cluster["score"]}
            break
        except Exception as e:  # noqa: BLE001
            print(f"[produce] story failed '{cluster['title']}': {e}")
    if not pkg:
        raise RuntimeError("Could not write a script for any of the top 3 stories")
    print(f"[produce] topic: {chosen['title']}  (LLM: {provider})")

    # 3. Voiceover, re-written shorter if it runs long
    voice = config.JOB / "voice.mp3"
    seconds = media.tts(pkg.script, voice)
    if seconds > config.MAX_AUDIO_SECONDS:
        print(f"[produce] audio {seconds:.1f}s too long, asking for a shorter script")
        pkg, provider = script_gen.write_package(chosen["title"], article, shorter=True)
        seconds = media.tts(pkg.script, voice)
    print(f"[produce] voice {seconds:.1f}s")

    # 4. Captions + stock clips
    words = media.transcribe_words(voice)
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
    description = script_gen.build_description(pkg, chosen["urls"])
    meta = {
        "job_id": job_id,
        "topic": chosen["title"],
        "llm": provider,
        "title": pkg.title_youtube,
        "description": description,
        "tags": pkg.tags,
        "hashtags": pkg.hashtags,
        "script": pkg.script,
        "sources": chosen["urls"],
        "pixabay": [c["pixabay_page"] for c in clips],
        "seconds": round(seconds, 1),
    }
    (config.OUT / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    # 8. Remember the topic so it isn't repeated this week
    trends.save_used(chosen["title"])

    # 9. Telegram approval request
    tg_video = _shrink_for_telegram(final)
    caption = (f"🎬 {meta['title']}\n\n📰 {meta['topic']}\n⏱ {meta['seconds']}s · 🤖 {provider}\n\n"
               f"Approve → posts at {publish_at} IST. Approve later → posts within ~15 min (until {closes}).")
    telegram_bot.send_video_for_approval(tg_video, caption, job_id)
    sources = "\n".join(meta["sources"]) or "(RSS snippets only, check facts carefully)"
    telegram_bot.send_message(f"📝 Script:\n{pkg.script}\n\n🔗 Sources:\n{sources}")
    print("[produce] done")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        telegram_bot.notify(f"⚠️ AI News Shorts: producing today's video failed.\n{type(e).__name__}: {e}"[:1500])
        sys.exit(1)
