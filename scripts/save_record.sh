#!/usr/bin/env bash
# Commit data/published.json (what was posted/approved/skipped). Safe to run often.
# Works even when the run was started by another workflow (detached checkout).
[ -n "${GITHUB_ACTIONS:-}" ] || exit 0
BRANCH="${DEFAULT_BRANCH:-main}"
git config user.name "ai-news-shorts-bot"
git config user.email "actions@users.noreply.github.com"
git add data/published.json
git diff --cached --quiet && exit 0
git commit -q -m "chore: update published record"
for i in 1 2 3 4 5; do
  git pull -q --rebase -X theirs origin "$BRANCH" && git push -q origin "HEAD:$BRANCH" && exit 0
  sleep $((i * 3))
done
echo "could not save published.json"
exit 1
