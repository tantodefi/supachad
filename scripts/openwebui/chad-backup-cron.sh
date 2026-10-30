#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Back up Chad's workspace state to GitHub (chad-state) from the host, every 6h via
# launchd (dev.nemoclaw.chad-backup). Deterministic on purpose: the in-pod
# workspace-backup cron asked the LLM to run the wrapper, and from 2026-10-25 it
# refused most runs (memory auto-recall injected old cron entries it treated as
# untrusted) — 2 of 12 runs backed up. The pod's /sandbox has no PVC, so a missed
# backup is lost state on the next pod recreate.
#
# Runs chad-backup-to-github in the foreground over ssh so the exit code is real.
# Logs to ~/.nemoclaw/openwebui/chad-backup.log; `chad-ops.sh doctor` checks freshness.

set -uo pipefail

ts() { date -u +%FT%TZ; }
echo "[$(ts)] backup start"
ssh -n -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=4 openshell-chad \
  '/usr/local/bin/chad-backup-to-github' >/tmp/chad-backup-host.$$ 2>&1
rc=$?
grep -vE '^\[backup\]   (skip|ok):' /tmp/chad-backup-host.$$ | tail -15
rm -f /tmp/chad-backup-host.$$
if [ "$rc" -eq 0 ]; then
  echo "[$(ts)] backup ok"
else
  echo "[$(ts)] backup FAILED (exit $rc)"
  command -v moshi-hook >/dev/null && moshi-hook notify --error --source chad-backup --title "Chad backup failed" \
    "chad-backup-to-github exit $rc — see ~/.nemoclaw/openwebui/chad-backup.log" >/dev/null 2>&1
fi
exit "$rc"
