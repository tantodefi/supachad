#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Conformance guard for the OpenShell-vault credential migration.
#
# The migration moved chad's secrets out of /sandbox/.nemoclaw/credentials.json
# and into the gateway vault, which injects them as ENV. That only works because
# every chad script reads its credential env-FIRST (falling back to the file just
# for the transition). If anyone reverts a script to read the file unconditionally,
# chad silently breaks after the file is gone. This test fails loudly on that.
#
# For each (script, ENV_VAR) pair: the script must reference the env var, and if
# it also references the credentials file it must read the env var first. Pure
# static check — no execution, no side effects. Runs anywhere bash + grep exist.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
fail=0
pass=0

# script (relative to scripts/) : env var it must read before the creds file
PAIRS=(
  "chad-backup-to-github.sh:GITHUB_TOKEN"
  "chad-restore-from-github.sh:GITHUB_TOKEN"
  "chad-clone-source.sh:GITHUB_TOKEN"
  "cron-wrappers:chad-cron-wrappers/chad-auth-context:NEMOCLAW_INVOKER_TOKEN"
  "cron-wrappers:chad-cron-wrappers/chad-premium-client:ANTHROPIC_API_KEY"
  "openwebui/chad-shim.py:CHAD_OPERATOR_ALLOWLIST"
)

check() {
  local rel="$1" var="$2" f="$here/$1"
  if [ ! -f "$f" ]; then
    echo "  FAIL $rel: file missing"
    fail=$((fail + 1))
    return
  fi
  # The env-var reference pattern (shell ${VAR} / $VAR, python os.environ/getenv).
  local env_re="(os\.environ(\.get)?\(?[\"']?${var}|\\\$\{?${var}|getenv\(\"?${var})"
  # Must read the env var at all — else vault injection is silently ignored.
  if ! grep -qE "$env_re" "$f"; then
    echo "  FAIL $rel: never reads \$$var from the environment (vault injection would be ignored)"
    fail=$((fail + 1))
    return
  fi
  # Find the first genuine *open/test* of the creds file — NOT the path
  # assignment (CREDS=...) and NOT comments/docstrings. These are the idioms that
  # actually touch the file: a -f/-r test, open(...), isfile(...), cat/jq/< redirect.
  # shellcheck disable=SC2016 # a literal regex, not an expansion
  local open_re='(\[[[:space:]]+-[fr][[:space:]]+"\$(CREDS|CREDS_FILE)"|open\(([^)]*)(CREDS|creds_path|CREDS_FILE)|isfile\(([^)]*)(CREDS|creds_path|CREDS_FILE)|cat[[:space:]]+"\$CREDS|jq[[:space:]].*"\$CREDS|<[[:space:]]*"\$CREDS)'
  local credline envline
  credline="$(grep -nE "$open_re" "$f" | head -1 | cut -d: -f1)"
  envline="$(grep -nE "$env_re" "$f" | head -1 | cut -d: -f1)"
  # If the file is ever opened, the env read must appear at or before that open.
  if [ -n "$credline" ] && [ -n "$envline" ] && [ "$envline" -gt "$credline" ]; then
    echo "  FAIL $rel: opens creds file (line $credline) before reading \$$var (line $envline) — not env-first"
    fail=$((fail + 1))
    return
  fi
  local where="env-only"
  [ -n "$credline" ] && where="env-first (file fallback at line $credline)"
  echo "  ok   $rel reads \$$var $where"
  pass=$((pass + 1))
}

for p in "${PAIRS[@]}"; do
  # Allow an optional "label:" prefix for readability in the array.
  case "$p" in
    cron-wrappers:*) p="${p#cron-wrappers:}" ;;
  esac
  check "${p%:*}" "${p##*:}"
done

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
