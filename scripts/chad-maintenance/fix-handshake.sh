#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Cold-start runbook step 2: after a cluster restart, Helm rotates
# OPENSHELL_SSH_HANDSHAKE_SECRET on the openshell StatefulSet, but the long-lived
# chad pod keeps its old value -> `ssh openshell-chad` fails. Point the
# StatefulSet back at the chad pod's value (rolls openshell-0, ~30s).
set -euo pipefail
K() { docker exec openshell-cluster-nemoclaw kubectl -n openshell "$@"; }
v=$(K exec chad -- printenv OPENSHELL_SSH_HANDSHAKE_SECRET)
[ -n "$v" ] || {
  echo "could not read chad pod's secret — aborting"
  exit 1
}
K set env statefulset/openshell OPENSHELL_SSH_HANDSHAKE_SECRET="$v"
K rollout status statefulset/openshell --timeout=180s
echo "ssh test:"
ssh -n -o ConnectTimeout=20 openshell-chad 'echo ssh-ok'
