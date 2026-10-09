"""Workflow 2: post an approved video to YouTube, Instagram and Facebook. The approval check happens first in
pipeline/gate.py; this only runs when you pressed Approve. Expects the produce artifact in ./out"""
import json
import sys
import traceback

from . import config, facebook, gate, instagram, telegram_bot, youtube


def run():
    meta_path = config.OUT / "meta.json"
    if not meta_path.exists():
        telegram_bot.notify("⚠️ AI News Shorts: no video found from today's produce run, nothing published.")
        return
    meta = json.loads(meta_path.read_text())

    results, errors = [], []
    thumb = config.OUT / "thumb_yt.jpg"
    try:
        video_id = youtube.upload(
            config.OUT / "final.mp4",
            meta["title"],
            meta["description"],
            meta["tags"],
            thumbnail_path=thumb if thumb.exists() else None,
        )
        note = "" if config.YT_PRIVACY == "public" else f" ({config.YT_PRIVACY} until the API audit is approved)"
        results.append(f"▶️ YouTube{note}: https://youtube.com/shorts/{video_id}")
    except Exception as e:  # noqa: BLE001 - one platform failing must not block the other
        traceback.print_exc()
        errors.append(f"YouTube: {type(e).__name__}: {e}")

    if instagram.enabled():
        try:
            _, link = instagram.publish_reel(config.OUT / "final.mp4", instagram.build_caption(meta))
            results.append(f"📸 Instagram: {link}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            errors.append(f"Instagram: {type(e).__name__}: {e}")

    if facebook.enabled():
        try:
            _, link = facebook.publish_reel(config.OUT / "final.mp4", facebook.build_description(meta))
            results.append(f"📘 Facebook: {link}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            errors.append(f"Facebook: {type(e).__name__}: {e}")

    if results:  # record it so later checks never post it twice
        gate.mark_done(meta["job_id"], "published")
    msg = f"✅ Published: {meta['title']}\n" + "\n".join(results)
    if errors:
        msg += "\n\n⚠️ Failed:\n" + "\n".join(errors)
    telegram_bot.notify(msg[:3900])
    if errors and not results:
        raise RuntimeError("All platforms failed")

if __name__ == "__main__":
    try:
        run()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        telegram_bot.notify(f"⚠️ AI News Shorts: publishing failed.\n{type(e).__name__}: {e}"[:1500])
        sys.exit(1)
