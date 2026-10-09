# AI News Shorts

Free, daily, automated 1-minute tech/AI news Shorts for an India + Asia audience.
Runs entirely on GitHub Actions. No server, no card.

```
6:00 AM + 3:00 PM IST  produce.yml (two videos a day)
  Google News RSS (IN, SG, MY, PH, PK + India searches) -> cluster & score -> skip topics used in last 7 days
  -> LLM picks the story (Gemini -> Groq -> OpenRouter fallback)
  -> LLM writes script, scenes, title, description, tags (facts only from the article)
  -> edge-tts voice (en-IN) -> faster-whisper word timings -> Pixabay clips
  -> Remotion renders 1080x1920 video with animated captions -> Playwright thumbnail
  -> Telegram: video + Approve / Reject buttons

8:00-11:00 AM + 7:00-10:00 PM IST  publish.yml (checks every 15 min)
  reads your button press -> uploads to YouTube (private until API audit approved)
  -> posts the Reel to Instagram and your Facebook Page (if their secrets are set) -> sends you the links
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
   | `CHANNEL_HANDLE` | `@tech_talk_hathim` | brand text on video + thumbnail (`none` = no name) |
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

## Facebook Page setup (optional)

Uses the same Meta app and token as Instagram.
1. In the Meta app, the **Manage everything on your Page** use case must include `pages_manage_posts`.
2. When generating the token in Graph API Explorer, also tick `pages_manage_posts`, then run `scripts/instagram_token.py` again.
3. Add secret `FB_PAGE_ID` (printed by the script). Update `IG_ACCESS_TOKEN` with the new token it prints.
4. Run **Check setup**: the Facebook line should show your Page name. Meta allows 30 API posts per Page per day.

## Test

1. **Actions -> Check setup -> Run workflow.** Every check should say OK (at least one LLM). You'll get a Telegram message.
2. **Actions -> Produce daily short -> Run workflow.** Takes ~10-20 min. The video arrives in Telegram. Press **Approve**.
3. **Actions -> Publish approved short -> Run workflow.** You get the YouTube link on Telegram.

After that it runs by itself every day. Change times by editing the `cron` lines (UTC; IST = UTC + 5:30).

## Daily routine (~5 minutes)

- Watch the video in Telegram, read the script and sources it sends.
- Approve before 8 AM / 7 PM -> posts exactly then. Approve later -> posts within ~15 minutes.
- Windows close at 11 AM and 10 PM. No answer by then = skipped (nothing gets posted).
- Already-posted videos are recorded in `data/published.json`, so nothing is ever posted twice.

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
