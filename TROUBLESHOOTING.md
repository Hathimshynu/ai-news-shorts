# Troubleshooting

| Telegram message / symptom | Cause | Fix |
|---|---|---|
| `groq: 401 Invalid API Key` | Wrong key (an xAI **Grok** key starts with `xai-`) | New key at console.groq.com (starts `gsk_`), update `GROQ_API_KEY` |
| `model no longer available` / `unavailable for free` (404) | Provider retired a model | Nothing: the router discovers current free models. Optional: set `GEMINI_MODEL` / `OPENROUTER_MODEL` variables |
| `503 high demand` / `429` | Provider busy | Automatic: next model/provider, then 2 retries |
| "All LLM providers failed … Fix these keys" | Every provider down or keys invalid | Fix the named keys; run **Check setup** |
| "No Approve press found for these videos" | Approve not pressed on those videos, or pressed > 24 h ago | Press ✅ Approve under the video, run **Publish approved short** |
| Approved but not posted yet | Outside 8-11 AM / 7-10 PM windows | Waits for the window, or run **Publish approved short** to post now |
| "⚠️ Partly published … Will retry" | One platform failed | Retries that platform at the next check (3 attempts) |
| "⚠️ QA FAILED" on a video | A blocking check failed (see report) | Reject it, or approve if you're happy; auto-publish never posts it |
| YouTube video private | API audit not approved yet | Keep `YT_PRIVACY` unset until approved, then set `public` |
| "captions not uploaded" in logs | Refresh token lacks `youtube.force-ssl` | Run `scripts/youtube_token.py`, update `YT_REFRESH_TOKEN` (optional) |
| Instagram/Facebook "insights unavailable" | Token lacks insights permissions | Add `instagram_manage_insights, read_insights` and rerun `scripts/instagram_token.py` (optional) |
| Scheduled runs don't start | GitHub delays/skips crons on new or idle repos | Run manually; it settles after a few days. Public repos idle 60 days get crons paused |
| Run fails at "Commit …" | Concurrent pushes | Retries automatically; never blocks a video |
| Telegram says webhook removed | A webhook blocked button presses | Don't set a webhook on this bot elsewhere |

## I tapped Approve but nothing happened
- **The button didn't change within ~1 minute** → the watcher isn't running. Actions → "Publish approved short": is a run in progress? If not, run it by hand (posts right away). Check the repo is **public** (private repos only check every 15 min).
- **The button says "posts at 7:00 PM"** → it's waiting for the window; nothing to do.
- **The button says "posting now" but no links arrive** → open the latest Publish run → "Check approvals" step; the error is also sent to Telegram.
- Several cancelled "Publish approved short" runs in the Actions list are normal: backup schedules that weren't needed because a watcher was already running.

## Videos arrive hours late / at strange times
GitHub's schedule (cron) can start runs hours late or skip them. Set up the two cron-job.org triggers
(README step 5); GitHub's schedule then only acts as a backup.

## "could not save published.json" / "You are not currently on a branch"
Fixed in this version (runs started by another workflow are now checked out on the main branch).
If you still see it, re-upload `scripts/save_record.sh` and `.github/workflows/publish.yml`.
