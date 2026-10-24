#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Container-model replacement for the chad-tunnel ssh -L job (cutover #44).
# Forwards the host ports OpenWebUI and the runs UI use to the sandbox's loopback
# services over the OpenShell gateway (gRPC), instead of ssh into the k3s pod:
#   127.0.0.1:8901  → chad-shim        (sandbox 127.0.0.1:8901)
#   127.0.0.1:18789 → OpenClaw gateway (sandbox 127.0.0.1:18791 in the container image)
#
# Runs both forwards in the foreground and exits when either dies, so launchd's
# KeepAlive restarts the pair (a backgrounded forward hides failures from launchd).
# Install at the cutover in place of dev.nemoclaw.chad-tunnel:
#   launchd/dev.nemoclaw.chad-tunnel-container.plist
set -uo pipefail

OPENSHELL="${CHAD_OPENSHELL_BIN:-/opt/homebrew/opt/openshell/bin/openshell}"
GW="${CHAD_GATEWAY_NAME:-nemoclaw-8088}"
SANDBOX="${CHAD_SANDBOX:-chad}"
SHIM_PORT="${CHAD_SHIM_PORT:-8901}"
GW_LOCAL="${CHAD_GATEWAY_LOCAL_PORT:-18789}"
GW_TARGET="${CHAD_GATEWAY_TARGET_PORT:-18791}"

"$OPENSHELL" -g "$GW" forward service "$SANDBOX" --target-port "$SHIM_PORT" --local "127.0.0.1:$SHIM_PORT" &
shim=$!
"$OPENSHELL" -g "$GW" forward service "$SANDBOX" --target-port "$GW_TARGET" --local "127.0.0.1:$GW_LOCAL" &
gw=$!
trap 'kill "$shim" "$gw" 2>/dev/null' EXIT TERM INT

# macOS /bin/bash is 3.2 (no `wait -n`): poll until either forward is gone.
while kill -0 "$shim" 2>/dev/null && kill -0 "$gw" 2>/dev/null; do sleep 5; done
echo "[chad-tunnel-container] a forward exited; exiting so launchd restarts both" >&2
exit 1
