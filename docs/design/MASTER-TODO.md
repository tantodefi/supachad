<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Chad master todo

Single list of everything open on Chad/supachad. Ordered by what unblocks what.
Status: `[ ]` open · `[~]` in progress · `[x]` done · `[U]` needs the user.
Last updated 2026-10-19 (evening).

## A. Memory + gbrain (broken now)

- [x] A1. Re-apply vector(2048) migration after the 10-19 wipe (`~/fix-gbrain-dims.sh`).
- [x] A2. Re-embed; 100% coverage (re-done after the second wipe).
- [x] A3. Durable dims fix (`gbrain-dims` in recover): a fresh `gbrain init` must create vector(2048) with no HNSW, so a
      wipe never needs a manual migration. Patch gbrain's schema in the image + a `chad-ops
      gbrain-dims` self-heal step in `recover`.
- [x] A4. Dream distillation (`chad-dream-distill`, shim injects `<slug>.context.md`): nightly dream only lists changed slugs. Add an LLM step that
      turns the day's chats/notes into actionable work-memories + per-operator context
      (tantodefi, tjcooke) as gbrain pages.
- [x] A5. Re-enable memory-lancedb (auto-recall + autoCapture) with the NVIDIA embedder and
      memory-wiki. active-memory stays off: it is a blocking sub-agent before every reply.
- [U] A6. The `-0313` "Starting Strength" series is really *Ferocious Fitness*: re-imported
      under the right name; run `~/fix-ff-mislabel.sh` to delete the mislabeled originals.
- [x] A7. Stale chat pages resurrected by restore → `chad-ops chat-prune` (now in recover).

## B. Reliability (the 10-19 wipe)

- [ ] B1. OOM headroom: Docker VM is 8 GB shared with WordPress + chad-next + OWUI. Stop the
      idle chad-next staging container; raise Docker VM memory at the next planned Docker
      restart [U: a Docker restart restarts the cluster → pod wipe; do it with the cutover].
- [x] B2. Backup skips the brain export when the database is unchanged.
- [x] B7. Inference route timeout 60s → 300s (long calls returned 503); enforced in recover.
- [x] B8. `autorecover` launchd job (10 min); heals shadow :8080 gateway + rotated handshake.
- [x] B3. recover: cron-reload after gateway restart; crons `--no-deliver`.
- [x] B4. Backup engine + 4 scripts were never deployed (not in manifest) → added.
- [x] B5. Backup PUT retry with sha refetch + GitHub error text.
- [x] B6. Docker image cleanup (84.5 → 22.1 GB).

## C. Paywall / onboarding

- [x] C1. Schedule the premium allowlist bridge (`sync-premium-allowlist.sh`, every 15 min,
      launchd). Today paid users never become premium.
- [x] C2. Atomic credit consume — already D1 (`a06787d`).
- [ ] C6. Move the Worker source from branch `feat/paywall-invite-api` onto main under `api/`.
- [U] C3. Square production webhook (`/api/square/webhook/prod`) + production keys live.
- [U] C4. Cloudflare token with Access:Edit for invite auto-grant (`CF_ACCESS_READY=1`).
- [U] C5. tjcooke OpenWebUI API key.

## D. Migration to OpenShell 0.0.116 (container model)

- [~] D1. #42 container-mode recover: runtime-bins, deploy-before-restore, shim relaunch,
      honest doctor/gate-check done. Open: 4 presets rejected on chad-next (agent-browser,
      github-tools, openclaw-bundled, session-logs); policy-sync only after the pod cluster stops.
- [ ] D2. #43 serving path chad-shim → container gateway.
- [U] D3. #44 production cutover (needs user present). Then: Proton re-login; re-check
      OpenClaw operator identity → MCP/exec; lift the chad-webui fail-closed gate.
- [x] D4. Pin tags pushed (`pin/v1-config-consumer-42a26d90`; `pin/pi-qualification-2be64a15` [U]).
- [U] D5. #37 chad-state repo ownership (tantodefi vs Supachad).

## E. Quality + docs

- [~] E1. Upstream merged into chad-dev (`23b6f0510f`, 0 regressions vs baseline+upstream).
      [U] push: the pre-push growth guardrail can't judge a fork merge (needs the user).
- [ ] E2. #49 dependency sweep (gbrain, gstack, skills, deps).
- [ ] E3. #47 skill review; #46 Smithers upgrade from 0.26.1.
- [ ] E4. #45 runs.supachad.com link in the OWUI user menu.
- [ ] E5. #52 docs refresh for the merged/migrated stack.
- [U] E6. supachad-docs: allow Actions to create PRs (repo setting).
