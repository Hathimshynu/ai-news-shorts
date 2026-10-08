"""Workflow 2: read the Telegram decision for today's video and upload it to YouTube if approved.
Expects the produce workflow's artifact downloaded into ./out"""
import json
import sys
import traceback

from . import config, telegram_bot, youtube


def run():
    meta_path = config.OUT / "meta.json"
    if not meta_path.exists():
        telegram_bot.notify("⚠️ AI News Shorts: no video found from today's produce run, nothing published.")
        return
    meta = json.loads(meta_path.read_text())
    decision = telegram_bot.get_decision(meta["job_id"])
    print(f"[publish] job {meta['job_id']} decision: {decision}")

    if decision != "approve":
        reason = "rejected" if decision == "reject" else "not approved in time"
        telegram_bot.notify(f"⏭ Skipped today's video ({reason}): {meta['title']}")
        return

    thumb = config.OUT / "thumb_yt.jpg"
    video_id = youtube.upload(
        config.OUT / "final.mp4",
        meta["title"],
        meta["description"],
        meta["tags"],
        thumbnail_path=thumb if thumb.exists() else None,
    )
    note = "" if config.YT_PRIVACY == "public" else f"\n(Uploaded as {config.YT_PRIVACY} until the API audit is approved.)"
    telegram_bot.notify(f"✅ Published: {meta['title']}\nhttps://youtube.com/shorts/{video_id}{note}")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        telegram_bot.notify(f"⚠️ AI News Shorts: publishing failed.\n{type(e).__name__}: {e}"[:1500])
        sys.exit(1)
