"""Stock clips (Pixabay), voiceover (edge-tts), captions (faster-whisper), helpers (ffmpeg)."""
import asyncio
import json
import subprocess
from pathlib import Path

import requests

from . import config

FALLBACK_KEYWORDS = ["technology abstract", "digital network", "computer code", "futuristic city", "circuit board"]


# ---------- ffmpeg helpers ----------
def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def extract_frame(video, out_jpg, at=1.0):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(at), "-i", str(video),
                    "-frames:v", "1", "-q:v", "3", str(out_jpg)], check=True)


# ---------- Pixabay ----------
def _search_pixabay(query):
    r = requests.get("https://pixabay.com/api/videos/", params={
        "key": config.PIXABAY_API_KEY, "q": query, "per_page": 10, "safesearch": "true",
    }, timeout=30)
    r.raise_for_status()
    return r.json().get("hits", [])


def _pick(hits, used_ids):
    def rendition(h):
        v = h["videos"]
        return v.get("medium") or v.get("small") or v.get("large")
    usable = [h for h in hits if h["id"] not in used_ids and rendition(h) and rendition(h).get("url")]
    # Prefer vertical, then longer clips.
    usable.sort(key=lambda h: (rendition(h)["height"] > rendition(h)["width"], h.get("duration", 0)), reverse=True)
    return (usable[0], rendition(usable[0])) if usable else (None, None)


def download_clips(scenes, dest: Path):
    """Download one clip per scene. Returns list of {file, seconds}."""
    dest.mkdir(parents=True, exist_ok=True)
    used_ids, result = set(), []
    for i, scene in enumerate(scenes):
        hit = rend = None
        for q in [scene.pixabay_keywords, " ".join(scene.pixabay_keywords.split()[:1]), *FALLBACK_KEYWORDS]:
            try:
                hit, rend = _pick(_search_pixabay(q), used_ids)
            except Exception as e:  # noqa: BLE001
                print(f"[pixabay] search failed '{q}': {e}")
            if hit:
                break
        if not hit:
            raise RuntimeError(f"No Pixabay clip found for scene {i}")
        used_ids.add(hit["id"])
        path = dest / f"clip{i}.mp4"
        with requests.get(rend["url"], stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        result.append({"file": f"job/clips/{path.name}", "seconds": probe_duration(path),
                       "pixabay_id": hit["id"], "pixabay_page": hit.get("pageURL", "")})
        print(f"[pixabay] scene {i}: {hit.get('pageURL')}")
    return result


# ---------- Voiceover ----------
def tts(text, out_mp3: Path):
    import edge_tts

    async def run():
        await edge_tts.Communicate(text, config.TTS_VOICE, rate=config.TTS_RATE).save(str(out_mp3))

    asyncio.run(run())
    return probe_duration(out_mp3)


# ---------- Captions ----------
def transcribe_words(audio: Path):
    from faster_whisper import WhisperModel

    model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio), language="en", word_timestamps=True, vad_filter=False)
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"word": text, "start": round(w.start, 3), "end": round(w.end, 3)})
    return words
