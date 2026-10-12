#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Focused test of the convergence-respawn logic in scripts/nemoclaw-start.sh.
# Extracts the REAL functions (gateway_exited_for_convergence +
# wait_openclaw_gateway_with_convergence_respawn) and drives them with a mock
# gateway, so we validate the actual shipped code paths without needing to
# trigger OpenClaw's intermittent first-run convergence refusal.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${1:-${_SELF_DIR}/nemoclaw-start.sh}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
GWLOG=/tmp/gateway.log   # the path the real functions hardcode

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

# --- Extract the two real functions verbatim from the entrypoint ---
awk '/^gateway_exited_for_convergence\(\) \{/{c=1}
     c{print}
     /^\}/{if(c==1 && seen_body){c=0; print ""}}
     c{seen_body=1}' "$SRC" >/dev/null 2>&1 || true
# Robust extraction by explicit line ranges around the function headers:
python3 - "$SRC" "$WORK/funcs.sh" <<'PY'
import re, sys
src, out = sys.argv[1], sys.argv[2]
text = open(src).read()
want = ["gateway_exited_for_convergence", "wait_openclaw_gateway_with_convergence_respawn"]
blocks = []
for name in want:
    m = re.search(r'^%s\(\) \{.*?^\}' % re.escape(name), text, re.S | re.M)
    if not m:
        sys.exit("could not extract %s" % name)
    blocks.append(m.group(0))
# also pull the _NEMOCLAW_GATEWAY_CONVERGENCE_MAX default assignment
m = re.search(r'^_NEMOCLAW_GATEWAY_CONVERGENCE_MAX=.*$', text, re.M)
if m:
    blocks.insert(0, m.group(0))
open(out, "w").write("\n".join(blocks) + "\n")
PY
[ -s "$WORK/funcs.sh" ] || fail "extraction produced empty file"
# shellcheck disable=SC1090
source "$WORK/funcs.sh"
type gateway_exited_for_convergence >/dev/null 2>&1 || fail "function not sourced"
pass "extracted + sourced real functions"

# --- Stubs for dependencies the loop references ---
# shellcheck disable=SC2034  # assigned here, read by the sourced respawn function
SANDBOX_WAIT_PID=""
refresh_openclaw_supervised_child_pids() { :; }

# --- Mock gateway launcher ---
# Writes to /tmp/gateway.log (truncating, like the real launcher) then backgrounds
# a short-lived process so GATEWAY_PID/wait behave like the real path.
MOCK_LAUNCH_COUNT=0
MOCK_CONVERGE_UNTIL=0   # launches 1..N emit the convergence refusal + exit 1
mock_launch() {
  MOCK_LAUNCH_COUNT=$((MOCK_LAUNCH_COUNT + 1))
  : > "$GWLOG"
  if [ "$MOCK_LAUNCH_COUNT" -le "$MOCK_CONVERGE_UNTIL" ]; then
    echo "OpenClaw plugin migration inputs changed during startup convergence; refusing to report the gateway ready. Restart OpenClaw." >>"$GWLOG"
    # shellcheck disable=SC2034  # read by the sourced respawn function via `wait`
    ( exit 1 ) & GATEWAY_PID=$!
  else
    echo "[gateway] ready" >>"$GWLOG"
    # shellcheck disable=SC2034  # read by the sourced respawn function via `wait`
    ( exit 0 ) & GATEWAY_PID=$!   # clean (non-convergence) exit ends the loop
  fi
}

# --- Test 1: converges after 2 refusals, then ready → returns 0, relaunched twice ---
MOCK_LAUNCH_COUNT=0; MOCK_CONVERGE_UNTIL=2
export NEMOCLAW_GATEWAY_CONVERGENCE_MAX=6
_NEMOCLAW_GATEWAY_CONVERGENCE_MAX=6
mock_launch   # initial launch (as the caller does before the wait helper)
wait_openclaw_gateway_with_convergence_respawn mock_launch; rc=$?
[ "$rc" -eq 0 ] || fail "T1 expected rc=0, got $rc"
[ "$MOCK_LAUNCH_COUNT" -eq 3 ] || fail "T1 expected 3 launches (1 initial + 2 respawns), got $MOCK_LAUNCH_COUNT"
pass "T1 converges after 2 refusals (3 launches, rc=0)"

# --- Test 2: ready on first launch → no respawn, returns 0 ---
MOCK_LAUNCH_COUNT=0; MOCK_CONVERGE_UNTIL=0
mock_launch
wait_openclaw_gateway_with_convergence_respawn mock_launch; rc=$?
[ "$rc" -eq 0 ] || fail "T2 expected rc=0, got $rc"
[ "$MOCK_LAUNCH_COUNT" -eq 1 ] || fail "T2 expected 1 launch (no respawn), got $MOCK_LAUNCH_COUNT"
pass "T2 ready on first launch (no respawn)"

# --- Test 3: persistent refusal is bounded by the cap (no infinite loop) ---
MOCK_LAUNCH_COUNT=0; MOCK_CONVERGE_UNTIL=999
_NEMOCLAW_GATEWAY_CONVERGENCE_MAX=3
mock_launch
wait_openclaw_gateway_with_convergence_respawn mock_launch; rc=$?
[ "$rc" -eq 1 ] || fail "T3 expected final rc=1 (gave up), got $rc"
# 1 initial + 3 bounded respawns = 4 launches
[ "$MOCK_LAUNCH_COUNT" -eq 4 ] || fail "T3 expected 4 launches (cap=3), got $MOCK_LAUNCH_COUNT"
pass "T3 persistent refusal bounded at cap=3 (4 launches, rc=1)"

# --- Test 4: detector matches both message variants, rejects unrelated output ---
: > "$GWLOG"; echo "refusing to report the gateway ready" >>"$GWLOG"
gateway_exited_for_convergence || fail "T4a should match 'refusing to report'"
: > "$GWLOG"; echo "migration inputs changed during startup convergence" >>"$GWLOG"
gateway_exited_for_convergence || fail "T4b should match 'migration inputs changed'"
: > "$GWLOG"; echo "[gateway] ready" >>"$GWLOG"
gateway_exited_for_convergence && fail "T4c must NOT match a clean ready log"
pass "T4 convergence detector matches signals, ignores clean exit"

rm -f "$GWLOG"
echo "ALL TESTS PASSED (4/4)"
