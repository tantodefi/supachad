#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# run-experiments.sh — host-side nightly driver for the Smithers evolutionary
# experiment loop. Honors the cron-wrapper rule: this is the one-line entry the
# scheduler calls; Smithers itself is the durable detached driver, so there is
# no slow LLM work inside a cron agent turn.
#
# Steps:
#   1. flock so two nights never overlap (idempotent with Smithers' own resume).
#   2. `smithers up experiments.jsx` — durable; resumes a prior crashed run.
#   3. Post the leaderboard to OpenWebUI as an operator-visible note (the
#      experiment-night success criterion) via chad-webui, if available. The
#      post is best-effort and never fails the run.
#
# Usage: run-experiments.sh [--dry-run]
#
# Env: see README. CHAD_WEBUI_BIN points at the chad-webui CLI; if unset/missing
# the artifact post is skipped (run still records to experiments.db + state/).

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || exit 1

LOCK="/tmp/chad-experiment-smithers.lock.d"
DRY="${1:-}"
SMITHERS="${SMITHERS_BIN:-$HERE/node_modules/.bin/smithers}"
REPORT="${CHAD_EXPERIMENT_REPORT:-$HERE/state/last-report.md}"

# ── Inference backend for the cron context ──────────────────────────────────
# Under launchd the `claude` CLI can't auth (subscription is keychain/GUI-bound)
# and its user hooks pollute headless output. So nightly runs use Nemotron Ultra
# via the NVIDIA API directly — the model we actually want to evaluate, no
# keychain, no hooks. Read NVIDIA_API_KEY from host credentials at runtime (kept
# out of the plist). Override CHAD_CAPABLE_BACKEND to keep claude if running
# interactively with a logged-in shell.
CREDS="${CHAD_HOST_CREDS:-/Users/r/.nemoclaw/credentials.json}"
if [ -z "${NVIDIA_API_KEY:-}" ] && [ -f "$CREDS" ]; then
  NVIDIA_API_KEY="$(python3 -c "import json;print(json.load(open('$CREDS')).get('NVIDIA_API_KEY',''))" 2>/dev/null || true)"
  export NVIDIA_API_KEY
fi
export CHAD_INFERENCE_BASE_URL="${CHAD_INFERENCE_BASE_URL:-https://integrate.api.nvidia.com/v1}"
export CHAD_CAPABLE_BACKEND="${CHAD_CAPABLE_BACKEND:-nemotron}"
export CHAD_CHEAP_BACKEND="${CHAD_CHEAP_BACKEND:-nemotron}"
# Headless: CLI agents (claude/codex/opencode) can't auth under launchd and the
# host claude hooks pollute output — keep selection AND fallback nemotron-only.
export CHAD_DISABLE_CLI_AGENTS="${CHAD_DISABLE_CLI_AGENTS:-1}"
# self-improve reads pod cron telemetry (~/.openclaw/cron-runs.jsonl) over ssh;
# without this it sees no signal and proposes nothing. Best-effort (graceful if absent).
export CHAD_SIGNAL_SSH="${CHAD_SIGNAL_SSH:-openshell-chad}"

# Portable atomic lock (mkdir succeeds for exactly one racer). flock is absent
# on macOS, where this runner lives; Smithers' own resume already dedupes the
# actual work, so this only guards against two scheduler fires colliding.
PIDF="$LOCK/pid"
if ! mkdir "$LOCK" 2>/dev/null; then
  # Steal a stale lock whose holder died (a SIGKILL'd run leaves the dir + the
  # EXIT trap never fires, which would block every future scheduler fire).
  HOLDER="$(cat "$PIDF" 2>/dev/null || true)"
  if [ -n "$HOLDER" ] && kill -0 "$HOLDER" 2>/dev/null; then
    echo "run-experiments: another run holds the lock (pid $HOLDER); exiting" >&2
    exit 0
  fi
  echo "run-experiments: clearing stale lock (holder ${HOLDER:-unknown} gone)" >&2
  rm -rf "$LOCK"
  if ! mkdir "$LOCK" 2>/dev/null; then
    echo "run-experiments: lock re-acquire race; exiting" >&2
    exit 0
  fi
fi
echo "$$" >"$PIDF"
trap 'rm -rf "$LOCK" 2>/dev/null' EXIT

if [ "$DRY" = "--dry-run" ]; then
  DRY_RUN=1 "$SMITHERS" up experiments.jsx
  DRY_RUN=1 "$SMITHERS" up workflows/token-optimize.jsx
  DRY_RUN=1 "$SMITHERS" up workflows/bug-report.jsx
  DRY_RUN=1 "$SMITHERS" up workflows/skill-improve.jsx
  echo "run-experiments: dry run complete (no state written, no artifact posted)"
  exit 0
fi

# Durable run. Smithers checkpoints every frame; a prior crashed run resumes.
"$SMITHERS" up experiments.jsx
rc=$?

# Best-effort operator-visible artifact. Never let a posting failure mask the run.
# chad-webui lives ON THE POD (not the host), so we stream the report over ssh
# and run it there. Verified 2026-06-13: notes create --content-file works; the
# note id is returned for later cleanup. CHAD_POD_SSH / CHAD_WEBUI_POD_BIN override.
POD_SSH="${CHAD_POD_SSH:-openshell-chad}"
POD_WEBUI="${CHAD_WEBUI_POD_BIN:-/sandbox/.openclaw-data/bin/chad-webui}"
if [ -f "$REPORT" ]; then
  title="Chad experiments — $(date -u +%Y-%m-%d)"
  # Capture the created note id to a log — `notes list` is currently broken
  # (HTTP 500), so the returned id is the only handle for later cleanup.
  note_out="$(ssh -o ConnectTimeout=15 "$POD_SSH" \
    "cat > /tmp/chad-exp-report.md && CHAD_WEBUI_TRUSTED=1 '$POD_WEBUI' notes create --title '$title' --content-file /tmp/chad-exp-report.md --tags chad-experiments" \
    <"$REPORT" 2>&1 || true)"
  note_id="$(printf '%s' "$note_out" | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)"
  if [ -n "$note_id" ]; then
    printf '%s\t%s\n' "$(date -u +%FT%TZ)" "$note_id" >>"$HERE/state/posted-notes.log"
    echo "run-experiments: posted leaderboard to OpenWebUI (note $note_id)"
  else
    echo "run-experiments: OpenWebUI post failed (run still recorded at $REPORT)" >&2
  fi
else
  echo "run-experiments: no report at $REPORT to post" >&2
fi

# Model benchmark (token-optimize) — OFF by default since 2026-10-24 (audit): in
# 259 runs it proposed 344 downgrades and applied none, and it could not save
# anything if it did. Chad's NVIDIA calls go through the sandbox inference route,
# which rewrites every request to ONE configured model, so per-task model choices
# never reach NVIDIA; NVIDIA-hosted inference is not billed per token; and its
# candidate models have since been withdrawn. CHAD_RUN_TOKENOPT=1 runs it.
if [ "${CHAD_RUN_TOKENOPT:-}" = "1" ]; then
  echo "run-experiments: running model benchmark (token-optimize, shadow)…" >&2
  CHAD_TOKENOPT_APPLY='' "$SMITHERS" up workflows/token-optimize.jsx >/dev/null 2>&1
  trc=$?
  # Smithers exit 3 = paused at its Approval gate, not a failure.
  if [ "$trc" -ne 0 ] && [ "$trc" -ne 3 ]; then
    echo "run-experiments: token-optimize benchmark non-fatal failure (rc=$trc)" >&2
  fi
fi

# Reporting loop — ONCE daily (the 05:00 invocation only) so the benchmark loop
# above can run 3x/day without spamming the approval queue. bug-report (own
# failures → issues), self-improve (cron telemetry → tunings; needs
# CHAD_SIGNAL_SSH for real signal), skill-improve (workflow enhancements). All
# shadow: they only ask for approval (Moshi) when their apply/post flag is on,
# because approving a shadow run changes nothing (164+57×3 unanswered gates by
# 2026-10-24). Their "would do" results are in the runs UI.
if [ "$(date +%H)" = "05" ]; then
  for wf in bug-report self-improve skill-improve; do
    echo "run-experiments: running $wf (shadow)…" >&2
    "$SMITHERS" up "workflows/$wf.jsx" >/dev/null 2>&1
    wrc=$?
    # rc 3 = paused at Approval gate (expected for these shadow workflows).
    if [ "$wrc" -ne 0 ] && [ "$wrc" -ne 3 ]; then
      echo "run-experiments: $wf non-fatal failure (rc=$wrc)" >&2
    fi
  done
fi

exit "$rc"
