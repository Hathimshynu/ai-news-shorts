"""Stock clips (Pixabay), voiceover (edge-tts), captions (faster-whisper), helpers (ffmpeg)."""
import asyncio
import difflib
import json
import re
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
def _edge_tts(text, out_mp3: Path):
    import edge_tts

    async def run():
        await edge_tts.Communicate(text, config.TTS_VOICE, rate=config.TTS_RATE, pitch=config.TTS_PITCH).save(str(out_mp3))

    asyncio.run(run())


def _rate_factor():
    """'+18%' -> 1.18 (used to speed up the backup voices the same way)."""
    try:
        return max(0.5, min(2.0, 1 + int(config.TTS_RATE.strip().rstrip("%")) / 100))
    except ValueError:
        return 1.0


def _speed_up(src: Path, dst: Path, factor):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-filter:a", f"atempo={factor:.3f}",
                    "-ar", "48000", str(dst)], check=True)


def _gtts(text, out_mp3: Path):
    """Backup 1: Google Translate voice, Indian English accent (free, no key)."""
    from gtts import gTTS

    raw = out_mp3.with_name("gtts_raw.mp3")
    gTTS(text, lang="en", tld="co.in").save(str(raw))
    _speed_up(raw, out_mp3, _rate_factor() * 1.12)  # gTTS speaks slowly; match the usual pace


def _piper(text, out_mp3: Path):
    """Backup 2: Piper, an offline voice that runs on the GitHub runner itself (female, British English)."""
    import urllib.request
    import wave

    from piper import PiperVoice

    base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alba/medium/"
    cache = Path.home() / ".cache" / "piper"
    cache.mkdir(parents=True, exist_ok=True)
    for f in ("en_GB-alba-medium.onnx", "en_GB-alba-medium.onnx.json"):
        if not (cache / f).exists():
            urllib.request.urlretrieve(base + f, cache / f)
    voice = PiperVoice.load(str(cache / "en_GB-alba-medium.onnx"))
    wav = out_mp3.with_name("piper.wav")
    with wave.open(str(wav), "wb") as w:
        if hasattr(voice, "synthesize_wav"):   # piper-tts 1.3+
            voice.synthesize_wav(text, w)
        else:                                  # older piper-tts
            voice.synthesize(text, w)
    _speed_up(wav, out_mp3, _rate_factor())


VOICE_USED = {"name": ""}  # shown in the Telegram message


def tts(text, out_mp3: Path):
    """Voiceover with automatic fallback: Microsoft Edge voice (3 tries) -> Google voice -> offline Piper.
    Microsoft sometimes blocks the free voice for hours (HTTP 403); the video is still made."""
    import time

    errors = []
    for attempt in range(3):
        try:
            _edge_tts(text, out_mp3)
            if out_mp3.exists() and out_mp3.stat().st_size > 1000:
                VOICE_USED["name"] = config.TTS_VOICE
                return probe_duration(out_mp3)
        except Exception as e:  # noqa: BLE001
            errors.append(f"edge-tts: {type(e).__name__}: {str(e)[:120]}")
            print(f"[tts] Microsoft voice failed (try {attempt + 1}/3): {errors[-1]}")
            time.sleep(10 * (attempt + 1))
    for name, fn in (("Google voice (backup)", _gtts), ("Piper offline voice (backup)", _piper)):
        try:
            fn(text, out_mp3)
            if out_mp3.exists() and out_mp3.stat().st_size > 1000:
                VOICE_USED["name"] = name
                print(f"[tts] used {name}")
                return probe_duration(out_mp3)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
            print(f"[tts] {errors[-1]}")
    raise RuntimeError("All voices failed: " + " | ".join(errors))


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


# ---------- Audio levelling ----------
def normalize_audio(src: Path, dst: Path, target_lufs=-14):
    """EBU R128 loudness normalisation (-14 LUFS is what YouTube/Instagram aim for)."""
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                    "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11", "-ar", "48000", str(dst)], check=True)
    return probe_duration(dst)


def loudness(path):
    """Integrated loudness (LUFS) and true peak (dBTP) of a file's audio."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "loudnorm=print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True)
    blob = r.stderr[r.stderr.rfind("{"): r.stderr.rfind("}") + 1]
    data = json.loads(blob)
    return float(data["input_i"]), float(data["input_tp"])


# ---------- Caption alignment ----------
def _tok(w):
    return re.sub(r"[^\w₹%]", "", w.lower())


def align_words(script, heard):
    """Captions use the SCRIPT's spelling with the timings whisper heard.
    Returns (words, coverage) where coverage = share of script words matched to speech (sync quality)."""
    script_words = script.split()
    if not heard:
        return [], 0.0
    a = [_tok(w) for w in script_words]
    b = [_tok(w["word"]) for w in heard]
    timing = [None] * len(script_words)
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            h = heard[blk.b + k]
            timing[blk.a + k] = (h["start"], h["end"])
    matched = sum(t is not None for t in timing)
    # fill gaps by spreading unmatched words evenly between known neighbours
    end_all = heard[-1]["end"]
    i = 0
    while i < len(timing):
        if timing[i] is not None:
            i += 1
            continue
        j = i
        while j < len(timing) and timing[j] is None:
            j += 1
        start = timing[i - 1][1] if i > 0 else heard[0]["start"]
        stop = timing[j][0] if j < len(timing) else end_all
        step = max(0.05, (stop - start) / (j - i))
        for k in range(i, j):
            s = start + (k - i) * step
            timing[k] = (round(s, 3), round(s + step * 0.9, 3))
        i = j
    words = [{"word": w, "start": t[0], "end": t[1]} for w, t in zip(script_words, timing)]
    return words, round(matched / len(script_words), 3)


def write_srt(words, path, per_line=6):
    def ts(t):
        h, rem = divmod(int(t * 1000), 3600000)
        m, rem = divmod(rem, 60000)
        s, ms = divmod(rem, 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"
    lines = []
    for n, i in enumerate(range(0, len(words), per_line), 1):
        chunk = words[i:i + per_line]
        lines += [str(n), f"{ts(chunk[0]['start'])} --> {ts(chunk[-1]['end'])}",
                  " ".join(w["word"] for w in chunk), ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")
