# Architecture

## Why GitHub Actions instead of a server + database
The original spec suggested Next.js + Node + PostgreSQL + Redis/BullMQ + Docker. Those need a machine
running 24/7, which costs money (no card-free free tier was available). This project keeps the same
responsibilities on free infrastructure:

| Spec component | Here |
|---|---|
| Scheduler / job queue (BullMQ) | GitHub Actions cron + `concurrency` groups (one job at a time per workflow) |
| Database (PostgreSQL) | JSON files in `data/`, committed by the workflows (schema below) |
| Approval UI + notifications | Telegram bot (buttons; polling, no webhook) |
| Dashboard (Next.js) | Static page `docs/index.html` on GitHub Pages, rebuilt nightly (read-only) |
| Video files between jobs | GitHub Actions artifacts (kept 3 days) |
| Docker deployment | `Dockerfile` for running the producer anywhere (optional) |

If you later get a server, the Python modules can run unchanged behind any scheduler; only the
`data/*.json` helpers (`gate.py`, `trends.py`, `analytics.py`) would move to a real database.

## Modules (`pipeline/`)
| File | Role |
|---|---|
| `trends.py` | collect from 7 source types, cluster duplicates, score, backlog, used-topic memory |
| `llm.py` | Gemini → Groq → OpenRouter router; live model discovery; retry/backoff; skips bad keys/retired models |
| `script_gen.py` | topic pick + category, script package (Pydantic-validated), fact-check with verified quotes |
| `media.py` | Pixabay clips, edge-tts, loudness normalisation, faster-whisper timings, caption alignment, SRT |
| `qa.py` | 13 automated checks → pass/fail report |
| `produce.py` | orchestrates one video end to end, sends it for approval |
| `gate.py` | stdlib-only approval check every 15 min; picks the approved video; auto-publish mode |
| `publish.py` | posts to YouTube/Instagram/Facebook with per-platform idempotency |
| `youtube.py`, `instagram.py`, `facebook.py` | official APIs (resumable uploads) |
| `analytics.py`, `report.py`, `dashboard.py` | nightly metrics, Telegram report, dashboard page |
| `check_setup.py` | tests every key/service |

## Data files (`data/`) — the "database"
**`used_topics.json`** — list, last 30 days: `{title, category, date}`. Used for duplicate-topic detection
(7 days) and category variety.

**`backlog.json`** — up to 8 runner-up stories, each a source item plus `saved` (ISO time); expires after 48 h.

**`trends_latest.json`** — last selection: `{job_id, chosen, reason, candidates:[{title, score, kinds, primary, links}]}`.

**`published.json`** — one record per video id (`YYYY-MM-DD-slot-HHMM`):
```json
{"status": "new|posting|published|rejected|expired|failed",
 "attempts": 1,
 "platforms": {"youtube": "https://…", "instagram": "https://…", "facebook": "https://…"},
 "ids": {"youtube": "abc", "instagram": "178…", "facebook": "123…"},
 "info": {"title": "…", "topic": "…", "category": "…", "hook": "…", "seconds": 41.2,
          "primary_keyword": "…", "qa_passed": true, "posted_at": "…"}}
```

**`analytics.json`** — per video id: `{info, links, platforms:{youtube|instagram|facebook:{views, likes,
comments, shares, saves, reach, engagement_rate}}, history:[{date, views}], fetched}`.

Engagement rate = (likes + comments + shares + saves) / views × 100, only when views are known.

Each video's full package (script, hooks, scenes, pronunciation, titles, keywords, fact-check claims,
QA report, sources) is in `meta.json` inside its GitHub artifact.

## Reliability
- **LLM**: busy/rate-limited (429/5xx) → next model → next provider; 3 rounds with 30 s / 60 s waits.
- **Idempotent publishing**: a platform is written to `published.json` immediately after it succeeds;
  retries only post missing platforms (max 3 attempts); `concurrency: publish` prevents overlapping runs.
- **Approval matching**: Telegram button data carries the unique video id; the gate downloads every
  video from the last 24 h and posts the one you approved.
- **Commits** retry with `pull --rebase` and never fail a run that already sent a video.
- **Alerts**: any failure sends a Telegram message; nightly report lists items needing attention.
- **Token refresh**: YouTube refresh token (app in Production = no 7-day expiry); Meta Page token never expires.

## Known limits
- YouTube retention/average-view-duration needs the YouTube Analytics API (not wired; Studio shows it).
- Instagram/Facebook reach & saves need `instagram_manage_insights` / `read_insights` on the token.
- YouTube caption-file upload needs the `youtube.force-ssl` scope (`scripts/youtube_token.py`); captions are
  burned into the video regardless.
- Reddit often blocks cloud servers; the other sources still work.
