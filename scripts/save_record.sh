#!/usr/bin/env bash
# Commit data/published.json (what was posted/approved/skipped). Safe to run often.
[ -n "${GITHUB_ACTIONS:-}" ] || exit 0
git config user.name "ai-news-shorts-bot"
git config user.email "actions@users.noreply.github.com"
git add data/published.json
git diff --cached --quiet && exit 0
git commit -q -m "chore: update published record"
for i in 1 2 3; do git pull -q --rebase && git push -q && exit 0; sleep 5; done
echo "could not save published.json"
exit 1
