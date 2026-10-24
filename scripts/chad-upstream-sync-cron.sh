#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Cron wrapper for chad-upstream-sync.sh.
#
# chad-upstream-sync.sh calls `gh pr create`, which needs the REPO OWNER's token
# (tantodefi) — Chad's "Supachad" gh account is a limited collaborator and cannot
# create PRs. The owner's classic token lives in the macOS keychain (what git uses),
# so we pull it and export it as GH_TOKEN for this run only. See the
# feedback_github_owner_vs_chad_auth memory.
#
# Scheduled weekly by dev.nemoclaw.chad-upstream-sync.plist. The underlying script
# only opens a PR when origin/chad-dev is behind upstream/main, and NEVER auto-merges
# — a human reviews and merges. Safe to run unattended.

set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

tok="$(printf 'protocol=https\nhost=github.com\n\n' | git -C "$here/.." credential fill 2>/dev/null | grep '^password=' | cut -d= -f2- || true)"
if [ -z "$tok" ]; then
  echo "[upstream-sync-cron] ERROR: no owner GitHub token in keychain; cannot open PR" >&2
  exit 1
fi
export GH_TOKEN="$tok"

# Run in a dedicated worktree, never in the working checkout: the sync script
# refuses a dirty tree (it skipped 2026-10-19 because a session had edits open)
# and `git checkout -b`s its sync branch, which would switch branches under
# whoever is working there. The worktree is reset to origin/chad-dev each run.
repo="$(cd "$here/.." && pwd)"
wt="${CHAD_UPSTREAM_SYNC_WORKTREE:-$HOME/.nemoclaw/worktrees/upstream-sync}"
git -C "$repo" fetch --quiet origin
if [ ! -e "$wt/.git" ]; then
  mkdir -p "$(dirname "$wt")"
  git -C "$repo" worktree add --quiet --detach "$wt" origin/chad-dev
fi
git -C "$wt" checkout --quiet --detach --force origin/chad-dev
git -C "$wt" clean -fdq -e node_modules
# Let the gate (make check) use the main checkout's installed dependencies.
[ -e "$wt/node_modules" ] || ln -s "$repo/node_modules" "$wt/node_modules"
# .gitignore's `node_modules/` only matches a directory; the symlink is a file.
ex="$(git -C "$wt" rev-parse --git-path info/exclude)"
grep -qx '/node_modules' "$ex" 2>/dev/null || echo '/node_modules' >>"$ex"
exec "$wt/scripts/chad-upstream-sync.sh" "$@"
