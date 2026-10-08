# AI News Shorts

Free, daily, automated 1-minute tech/AI news Shorts for an India + Asia audience.
Runs entirely on GitHub Actions. No server, no card.

```
3:00 PM IST  produce.yml
  Google News RSS (IN, SG, MY, PH, PK + India searches) -> cluster & score -> skip topics used in last 7 days
  -> LLM picks the story (Gemini -> Groq -> OpenRouter fallback)
  -> LLM writes script, scenes, title, description, tags (facts only from the article)
  -> edge-tts voice (en-IN) -> faster-whisper word timings -> Pixabay clips
  -> Remotion renders 1080x1920 video with animated captions -> Playwright thumbnail
  -> Telegram: video + Approve / Reject buttons

7:00 PM IST  publish.yml
  reads your button press -> uploads to YouTube (private until API audit approved)
  -> posts the Reel to Instagram (if IG secrets are set) -> sends you the links
```

## Setup (one time)

1. Create a **private** GitHub repo and upload everything in this folder (keep the `.github` folder).
2. **Settings -> Secrets and variables -> Actions -> Secrets**, add:

   | Secret | Value |
   |---|---|
   | `GEMINI_API_KEY` | Google AI Studio key |
   | `GROQ_API_KEY` | console.groq.com key (Groq, not Grok) |
   | `OPENROUTER_API_KEY` | OpenRouter key |
   | `PIXABAY_API_KEY` | Pixabay key |
   | `TELEGRAM_BOT_TOKEN` | from BotFather |
   | `TELEGRAM_CHAT_ID` | your chat id |
   | `YT_CLIENT_ID` / `YT_CLIENT_SECRET` | from client_secret.json |
   | `YT_REFRESH_TOKEN` | from the Colab script (starts with `1//`) |

3. Optional **Variables** tab:

   | Variable | Example | Purpose |
   |---|---|---|
   | `CHANNEL_HANDLE` | `@yourchannel` | brand text on video + thumbnail |
   | `YT_PRIVACY` | `public` | set only after the YouTube API audit is approved |
   | `AFFILIATE_FOOTER` | `My AI ebook: https://...` | added to every description |
   | `TTS_VOICE` | `en-IN-NeerjaNeural` | female Indian English voice |

4. **Settings -> Actions -> General -> Workflow permissions**: choose **Read and write permissions** (the produce run saves used topics).
5. Optional: put 2-3 royalty-free `.mp3` tracks (YouTube Audio Library) in `renderer/public/music/`. One is picked at random at low volume.

## Instagram setup (optional)

Requirements: Instagram **Professional** account (Creator or Business), linked to a **Facebook Page** you manage.

1. developers.facebook.com -> My Apps -> Create app -> use case **Other** -> type **Business**.
2. In the app, add the **Instagram** product (API setup with **Facebook login**).
3. Open **Tools -> Graph API Explorer**, pick your app, click **Generate Access Token**, and add the permissions:
   `instagram_basic, instagram_content_publish, pages_show_list, pages_read_engagement, business_management`.
   Tick your Page and your Instagram account when Facebook asks.
4. Run `scripts/instagram_token.py` (paste it into a Colab code cell). Enter App ID, App Secret (App settings -> Basic) and the token.
5. Add the two values it prints as secrets `IG_USER_ID` and `IG_ACCESS_TOKEN`.
6. Run **Check setup**: the Instagram line should say `OK account = @yourname`.

The app can stay in Development mode because it only posts to your own account. Instagram allows up to 50 API posts per day.

## Test

1. **Actions -> Check setup -> Run workflow.** Every check should say OK (at least one LLM). You'll get a Telegram message.
2. **Actions -> Produce daily short -> Run workflow.** Takes ~10-20 min. The video arrives in Telegram. Press **Approve**.
3. **Actions -> Publish approved short -> Run workflow.** You get the YouTube link on Telegram.

After that it runs by itself every day. Change times by editing the `cron` lines (UTC; IST = UTC + 5:30).

## Daily routine (~5 minutes)

- Watch the video in Telegram, read the script and sources it sends.
- Approve or Reject before 7 PM. No answer = skipped (nothing bad gets posted).
- Buttons don't show a confirmation instantly; the publish run reads them at 7 PM.

## Notes and limits

- **Uploads are private** until YouTube approves your API audit. Then set the `YT_PRIVACY` variable to `public`.
- **AI disclosure** is sent automatically (`containsSyntheticMedia`). If YouTube ignores it, tick "Altered or synthetic content" in Studio.
- **Never set a webhook** on the Telegram bot; approval uses polling.
- GitHub may start scheduled runs 5-30 minutes late. Free minutes for private repos: 2,000/month; this uses roughly 450-700.
- Model names change. If an LLM fails in "Check setup", update `GEMINI_MODEL` / `GROQ_MODEL` / `OPENROUTER_MODEL` (Variables or `pipeline/config.py`).
- Pixabay asks that you don't hotlink; clips are downloaded per run, and their pages are listed in `meta.json`.

## Customise

| What | Where |
|---|---|
| Countries / search topics | `NEWS_COUNTRIES`, `NEWS_QUERIES` in `pipeline/config.py` |
| Script style and rules | `WRITE_SYSTEM` in `pipeline/script_gen.py` |
| Video look (fonts, colours, captions) | `renderer/src/NewsShort.tsx` (preview: `cd renderer && npm run studio`) |
| Thumbnail design | `templates/thumb.html` |

## Run locally (optional)

```bash
pip install -r requirements.txt && python -m playwright install --with-deps chromium
cd renderer && npm install && cd ..
export GEMINI_API_KEY=... PIXABAY_API_KEY=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
python -m pipeline.produce
```
