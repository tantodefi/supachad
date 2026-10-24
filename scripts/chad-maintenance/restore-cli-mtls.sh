#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Restore host openshell CLI mTLS for the live 0.0.20 cluster gateway (task #36).
# The cutover re-enrolled ~/.config/openshell/gateways/nemoclaw/mtls against the
# 0.0.116 brew gateway; the 0.0.20 gateway's own client bundle lives in the
# in-cluster secret openshell-client-tls. Verifies BEFORE swapping; backs up first.
set -euo pipefail
D=~/.config/openshell/gateways/nemoclaw/mtls
T=$(mktemp -d)
chmod 700 "$T"
trap 'rm -rf "$T"' EXIT

for k in ca.crt tls.crt tls.key; do
  docker exec openshell-cluster-nemoclaw kubectl get secret -n openshell openshell-client-tls \
    -o "jsonpath={.data.${k//./\\.}}" | base64 -d >"$T/$k"
done

echo "verify against live :8080 gateway:"
if ! echo | openssl s_client -connect 127.0.0.1:8080 -CAfile "$T/ca.crt" \
  -cert "$T/tls.crt" -key "$T/tls.key" 2>/dev/null | grep -q 'Verify return code: 0'; then
  echo "  ✗ secret bundle does NOT verify against :8080 — not swapping"
  exit 1
fi
echo "  ✓ verifies"

B="$D.0116-bak-$(date +%Y%m%d%H%M%S)"
cp -Rp "$D" "$B" && echo "backed up 0.0.116 certs → $B"
install -m 644 "$T/ca.crt" "$D/ca.crt"
install -m 644 "$T/tls.crt" "$D/tls.crt"
install -m 600 "$T/tls.key" "$D/tls.key"
echo "restored. check:"
openshell sandbox list
