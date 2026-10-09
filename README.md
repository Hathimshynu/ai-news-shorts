# Tech Talk Hathim · AI News Shorts

A free, automated daily content engine: it finds trending AI and tech stories, fact-checks them,
makes a 30-55 second vertical video with voice and captions, runs quality checks, asks you for
approval on Telegram, then posts to **YouTube Shorts, Instagram Reels and your Facebook Page**.
It runs on GitHub Actions (no server, no card). Your phone only needs Telegram.

```
6:00 AM / 3:00 PM IST   produce.yml (one video each run)
  Trend discovery  Google News (IN + Asia) · OpenAI/Google/DeepMind/Microsoft/NVIDIA/Hugging Face/GitHub blogs
                   · Hacker News · Reddit · GitHub rising AI repos · Product Hunt · Google Trends India
  -> merge duplicates, score (credibility, recency, India relevance, buzz), skip recent topics
  -> AI editor picks the story + 1 of 10 categories (keeps variety)       [Gemini -> Groq -> OpenRouter]
  -> script package: 3 hooks, script, CTA, scenes, pronunciation, 3 titles, keywords, captions text
  -> fact-check every claim against the source text (quotes verified by code), auto-rewrite once
  -> voice (edge-tts, en-IN) -> loudness -14 LUFS -> captions aligned to the script -> Pixabay clips
  -> Remotion renders 1080x1920 -> thumbnails -> 13 automated quality checks
  -> Telegram: video + Approve/Reject, script, hook options, fact-check, QA report, sources

right after each video  publish.yml starts and watches Telegram until 11 AM / 10 PM IST
  -> every tap is confirmed within ~10 s (button changes + pop-up)
  -> posts the video YOU approved to YouTube (+captions file), Instagram, Facebook (+cover):
     at 8 AM / 7 PM if approved early, within a minute if approved during the window
  -> each platform recorded the moment it succeeds: nothing is ever posted twice

10:15 PM IST            report.yml
  -> collects views/likes/comments -> Telegram daily report -> rebuilds the dashboard (GitHub Pages)
```

## Setup

### 1. Secrets (Settings → Secrets and variables → Actions → Secrets)

| Secret | Where to get it |
|---|---|
| `GEMINI_API_KEY` | aistudio.google.com → API keys |
| `GROQ_API_KEY` | console.groq.com → API Keys (starts with `gsk_`) |
| `OPENROUTER_API_KEY` | openrouter.ai → Keys |
| `PIXABAY_API_KEY` | pixabay.com/api/docs (logged in) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | @BotFather; chat id from `api.telegram.org/bot<TOKEN>/getUpdates` |
| `YT_CLIENT_ID`, `YT_CLIENT_SECRET` | Google Cloud → OAuth client (Desktop app) JSON |
| `YT_REFRESH_TOKEN` | run `scripts/youtube_token.py` in Google Colab |
| `IG_USER_ID`, `IG_ACCESS_TOKEN`, `FB_PAGE_ID` | run `scripts/instagram_token.py` in Colab (see below) |

Only one of the three AI keys is required; more keys = more fallback when one is busy.

### 2. Variables (same page → Variables tab), all optional

| Variable | Example | Effect |
|---|---|---|
| `CHANNEL_HANDLE` | `@tech_talk_hathim` | name on videos, thumbnails, captions (`none` hides it) |
| `TTS_VOICE` | `en-IN-NeerjaNeural` | female Indian English voice (default male `en-IN-PrabhatNeural`) |
| `TTS_RATE` | `+25%` | speaking speed (default `+18%`) |
| `YT_PRIVACY` | `public` | set after the YouTube API audit is approved (until then uploads are private) |
| `AUTO_PUBLISH` | `true` | post videos that pass all quality checks without pressing Approve (Reject still works) |
| `AFFILIATE_FOOTER` | `My AI ebook: https://…` | added to every YouTube description |

### 3. Settings
- **Settings → Actions → General → Workflow permissions → Read and write**.
- **Make the repo public** (Settings → General): unlimited free Actions minutes and free GitHub Pages. Secrets stay hidden.
- **Dashboard:** Settings → Pages → Source "Deploy from a branch" → `main` / `/docs`. Then it's at `https://<you>.github.io/<repo>/` (updated nightly; run **Daily report and dashboard** to build it now).

### 4. Instagram + Facebook (one Meta app, one token)
1. Instagram must be a **Professional** account linked to your **Facebook Page**.
2. developers.facebook.com → Create app → use cases **Manage messaging & content on Instagram** and **Manage everything on your Page**.
3. Tools → Graph API Explorer → your app → User token with `instagram_basic, instagram_content_publish, pages_show_list, pages_read_engagement, pages_manage_posts, business_management` (optional for analytics: `instagram_manage_insights, read_insights`).
4. Run `scripts/instagram_token.py` in Colab → add `IG_USER_ID`, `IG_ACCESS_TOKEN`, `FB_PAGE_ID`.

### 5. Test
1. **Actions → Check setup → Run workflow** → every line OK (at least one AI provider).
2. **Actions → Produce daily short → Run workflow** (~15 min) → video in Telegram → **Approve**.
3. **Actions → Publish approved short → Run workflow** → links in Telegram.

## Daily use (≈ 5 minutes)
- Watch the video, read the fact-check and QA report Telegram sends with it.
- Tap **Approve**: within ~10 seconds the button changes to "✅ Approved · posts at 8:00 AM" (with a Cancel button) or "✅ Approved · posting now". If it doesn't change, the approval wasn't seen: see TROUBLESHOOTING.md.
- Approve before 8 AM / 7 PM → posts then. Approve during the window → posts within a minute. Windows close 11 AM / 10 PM.
- Tap **Reject** → Telegram sends the top 10 trending topics as buttons. Tap one and a new video on that topic arrives for approval in ~15 min (marked 🎯 Your pick). One replacement per rejected video.
- Missed the window? The video is marked skipped, but tapping Approve later still posts it in the next window (or run **Publish approved short** by hand to post immediately).
- The repo must be **public** for the live watcher (free unlimited minutes). A private repo falls back to checks every 15 minutes.
- QA-failed videos are marked ⚠️; you can still approve them yourself, but auto-publish never posts them.
- Pin the suggested comment on YouTube (Telegram sends it after posting).

## Quality checks (every video)
Blocking: 1080x1920, 15-60 s, audio present, loudness -18…-11 LUFS, no clipping, cover frame not dark,
captions ≥70% in sync with the voice, fact-check passed, ≥1 source link.
Advisory: official/primary source, keyword in title, 3-8 hashtags, different from recent topics.

## What's free and what isn't
Everything here runs on free tiers: GitHub Actions, Gemini/Groq/OpenRouter free models, edge-tts, Pixabay,
Telegram, YouTube Data API (10,000 units/day ≈ 6 uploads), Instagram (50 API posts/day), Facebook (30/day).
Free tiers change: if a provider removes its free model the router skips it automatically, but if all three
stop offering free models you'd need a paid key. Not automated on purpose: logging into products to record
real screen demos (against their terms). Videos use accurate stock footage + text instead.

## Customise
| What | Where |
|---|---|
| News countries / searches | `NEWS_COUNTRIES`, `NEWS_QUERIES` in `pipeline/config.py` |
| Official blog feeds, source weights | `OFFICIAL_FEEDS`, `KIND_WEIGHT` in `pipeline/trends.py` |
| Script style, categories | `WRITE_SYSTEM`, `CATEGORIES` in `pipeline/script_gen.py` |
| Quality thresholds | `pipeline/qa.py` |
| Video look | `renderer/src/NewsShort.tsx` (preview: `cd renderer && npm run studio`) |
| Thumbnail / dashboard design | `templates/thumb.html`, `templates/dashboard.html` |
| Schedules | `cron` lines in `.github/workflows/*.yml` (UTC; IST = UTC + 5:30) |

More: [ARCHITECTURE.md](ARCHITECTURE.md) (design, data files, reliability) · [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Run locally / Docker / tests
```bash
cp .env.example .env            # fill in keys
docker build -t shorts . && docker run --env-file .env -v "$PWD/out:/app/out" shorts   # one video, no GitHub needed
pip install -r requirements.txt pytest && python -m pytest -q                               # tests
```
