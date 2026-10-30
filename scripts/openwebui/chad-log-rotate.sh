#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Rotate the host-side Chad logs daily (launchd dev.nemoclaw.chad-log-rotate).
# newsyslog needs root, so this is copytruncate in user space: a log over MAX_MB is
# gzipped to <log>.1.gz (one generation kept) and truncated in place. Truncating is
# safe for launchd StandardOutPath files and the scripts' own >> logs — both write
# with O_APPEND, so the next write lands at the new end of file.
#
# The 8088 openshell-gateway was the reason: one INFO line a minute, 150+ MB.
#
# Env: MAX_MB (default 20)

set -uo pipefail

MAX_BYTES=$((${MAX_MB:-20} * 1024 * 1024))
shopt -s nullglob
for log in "$HOME"/.nemoclaw/openwebui/*.log "$HOME"/Library/Logs/openshell-gateway*.log; do
  size=$(stat -f %z "$log" 2>/dev/null) || continue
  [ "$size" -gt "$MAX_BYTES" ] || continue
  if gzip -c "$log" >"$log.1.gz.tmp"; then
    mv "$log.1.gz.tmp" "$log.1.gz"
    : >"$log"
    echo "[$(date -u +%FT%TZ)] rotated $log ($((size / 1024 / 1024)) MB)"
  else
    rm -f "$log.1.gz.tmp"
    echo "[$(date -u +%FT%TZ)] FAILED to rotate $log" >&2
  fi
done
