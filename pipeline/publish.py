"""Workflow 2: post an approved video to YouTube, Instagram and Facebook.
pipeline/gate.py decides first; this only runs after Approve (or to finish an interrupted post).

Duplicate protection: each platform's result is saved to data/published.json the moment it
succeeds, so a retry, a second Approve press or an overlapping run only posts to the
platforms that are still missing. Failed platforms are retried at the next 15-minute check
(up to 3 attempts in total)."""
import json
import sys
import traceback

from . import config, facebook, gate, instagram, telegram_bot, youtube


def _youtube(meta):
    thumb = config.OUT / "thumb_yt.jpg"
    vid = youtube.upload(config.OUT / "final.mp4", meta["title"], meta["description"], meta["tags"],
                         thumbnail_path=thumb if thumb.exists() else None)
    return f"https://youtube.com/shorts/{vid}"


def _instagram(meta):
    return instagram.publish_reel(config.OUT / "final.mp4", instagram.build_caption(meta))[1]


def _facebook(meta):
    return facebook.publish_reel(config.OUT / "final.mp4", facebook.build_description(meta),
                                 cover_path=config.OUT / "thumb_vertical.jpg")[1]


def run():
    meta_path = config.OUT / "meta.json"
    if not meta_path.exists():
        print("[publish] no video downloaded, nothing to do")
        return
    meta = json.loads(meta_path.read_text())
    job_id = meta["job_id"]

    rec = gate.get(job_id)
    if rec["status"] in gate.FINAL_STATES:
        print(f"[publish] {job_id} already {rec['status']}, not posting again")
        return
    rec["status"] = "posting"
    rec["attempts"] = rec.get("attempts", 0) + 1
    rec.setdefault("platforms", {})
    gate.put(job_id, rec)

    targets = [("YouTube", "youtube", _youtube)]
    if instagram.enabled():
        targets.append(("Instagram", "instagram", _instagram))
    if facebook.enabled():
        targets.append(("Facebook", "facebook", _facebook))

    errors = []
    for label, key, fn in targets:
        if key in rec["platforms"]:
            print(f"[publish] {label} already posted: {rec['platforms'][key]}")
            continue
        try:
            rec["platforms"][key] = fn(meta)
            gate.put(job_id, rec)  # save immediately so this platform is never posted twice
        except Exception as e:  # noqa: BLE001 - one platform failing must not block the others
            traceback.print_exc()
            errors.append(f"{label}: {type(e).__name__}: {str(e)[:300]}")

    icons = {"youtube": "▶️ YouTube", "instagram": "📸 Instagram", "facebook": "📘 Facebook"}
    lines = [f"{icons[k]}: {v}" for k, v in rec["platforms"].items()]
    if "youtube" in rec["platforms"] and config.YT_PRIVACY != "public":
        lines.append(f"(YouTube is {config.YT_PRIVACY} until the API audit is approved)")

    last_try = rec["attempts"] >= gate.MAX_ATTEMPTS
    if not errors or last_try:
        rec["status"] = "published" if rec["platforms"] else "failed"
        gate.put(job_id, rec)

    if errors:
        retry = "Giving up on these." if last_try else "Will retry the failed ones in ~15 minutes."
        head = f"⚠️ Partly published: {meta['title']}" if rec["platforms"] else f"❌ Publishing failed: {meta['title']}"
        telegram_bot.notify("\n".join([head, *lines, "", "Failed:", *errors, "", retry])[:3900])
    else:
        telegram_bot.notify("\n".join([f"✅ Published: {meta['title']}", *lines])[:3900])


if __name__ == "__main__":
    try:
        run()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        telegram_bot.notify(f"⚠️ AI News Shorts: publishing failed.\n{type(e).__name__}: {e}"[:1500])
        sys.exit(1)
