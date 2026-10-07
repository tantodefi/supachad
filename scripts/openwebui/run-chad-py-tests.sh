#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Runs the chad-specific Python test suites (OpenWebUI filter + shim gate).
# These cover logic the vitest suites don't reach — the billing credit-metering
# filter and the operator allowlist security boundary. No pytest required: each
# file is standalone-runnable with plain python3 (pydantic is the only dep, and
# it already ships in the OpenWebUI image). If pytest IS present it also works as
# `pytest scripts/openwebui`.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
rc=0
for t in "$here/functions/test_chad_rate_limit.py" "$here/test_chad_shim_allowlist.py"; do
  echo "=== ${t##*/} ==="
  python3 "$t" || rc=1
  echo
done
exit "$rc"
