#!/usr/bin/env bash
# Download every "short" video made in the last 24 hours into candidates/<run_id>/ (skips ones already here).
set -u
mkdir -p candidates
gh api "repos/${GITHUB_REPOSITORY}/actions/artifacts?name=short&per_page=10" \
  --jq '.artifacts[] | select(.expired == false) | select((.created_at | fromdateiso8601) > (now - 86400)) | .workflow_run.id' \
| sort -u | while read -r RID; do
    [ -f "candidates/$RID/meta.json" ] && continue
    gh run download "$RID" --name short --dir "candidates/$RID" || echo "could not download $RID"
  done
ls candidates
