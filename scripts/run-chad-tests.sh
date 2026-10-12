#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Runs the chad-specific test suites that the vitest projects don't reach:
# billing credit-metering, the operator allowlist security gate, the premium
# escalation client, and the vault-migration env-first conformance guard.
# No pytest required (pydantic is the only dep, already in the OpenWebUI image);
# each python file is standalone-runnable. Also works under pytest.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
rc=0
run() { echo "=== $1 ==="; shift; "$@" || rc=1; echo; }

run "billing credit-meter"        python3 "$here/openwebui/functions/test_chad_rate_limit.py"
run "operator allowlist gate"     python3 "$here/openwebui/test_chad_shim_allowlist.py"
run "premium escalation client"   python3 "$here/chad-cron-wrappers/test_chad_premium_client.py"
run "env-first conformance guard" bash   "$here/test_chad_cred_env_first.sh"
run "gateway convergence respawn" bash   "$here/test_gateway_convergence_respawn.sh"

echo "=================================="
[ "$rc" -eq 0 ] && echo "ALL CHAD SUITES PASSED" || echo "SOME CHAD SUITES FAILED"
exit "$rc"
