#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Remove the 313 brain pages wrongly titled "Starting Strength — chunk NNNN/0313".
# They are really "Ferocious Fitness" (Ross & Gallagher) and were re-imported on
# 2026-10-19 as ferocious-fitness-chunk-NNNN-0313. The real Starting Strength was
# ingested 2026-10-24 as starting-strength-chunk-NNNN-1030 and is not touched
# (only the -0313 slugs are deleted). Refuses unless the
# replacements exist (spot-checks first/middle/last). Then embeds the new pages.
set -euo pipefail
ssh -n openshell-chad 'set -e
for n in 0001 0157 0313; do
  gbrain get "ferocious-fitness-chunk-${n}-0313" 2>/dev/null | grep -q "Ferocious Fitness" \
    || { echo "refusing: ferocious-fitness-chunk-${n}-0313 missing"; exit 1; }
done
ok=0; bad=0
for i in $(seq -w 1 313); do
  n=$(printf "%04d" "$((10#$i))")
  if gbrain delete "starting-strength-chunk-${n}-0313" >/dev/null 2>&1; then ok=$((ok+1)); else bad=$((bad+1)); fi
done
echo "deleted=$ok failed=$bad"
nohup sh -c "HOME=/sandbox gbrain embed --stale > /tmp/gbrain-embed.log 2>&1" >/dev/null 2>&1 &
echo "embed started (log /tmp/gbrain-embed.log)"'
