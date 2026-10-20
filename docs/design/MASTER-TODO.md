<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Chad master todo

Single list of everything open on Chad/supachad. Ordered by what unblocks what.
Status: `[ ]` open · `[~]` in progress · `[x]` done · `[U]` needs the user.
Last updated 2026-10-20 (morning).

## A. Memory + gbrain

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
- [x] A8. Distilled context mixed operators. Cause: the chats were from tantodefi's account, but
      Chad's *replies* assumed he was the coach ("your Skool community") and the distiller took
      those guesses as facts. Fixed (`84ad6c43c0`): facts only from the user's own turns; Chad's
      turns trimmed and context-only. Re-distilled 10-20: profile correct, coaching facts gone.
- [ ] A9. "Open requests" in the distilled context lists finished one-off asks (trivia, kids' art).
      Tighten the prompt: only asks Chad hasn't completed.

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

Order: test everything in Square sandbox with test users → fix → switch to production → re-test live.

- [x] C1. Schedule the premium allowlist bridge (`sync-premium-allowlist.sh`, every 15 min,
      launchd). Today paid users never become premium.
- [x] C2. Atomic credit consume — already D1 (`a06787d`).
- [~] C6. Worker source onto main under `api/` — supachad-landing PR #2 [U: review + merge].
- [x] C7. Signup + invite tested (local Worker): signup recorded, admin-only mint, single-use
      redeem. Gap: signup sends NO email and nothing mints invites automatically — an admin
      mints each one by hand (C12).
- [x] C8. Free tier tested (live OWUI filter source vs local Worker): daily cap blocks with the
      upgrade CTA, admins/premium model exempt, credit users count down then fall back to the
      cap. There are no starter credits by design (free = 50 msgs/day). Free users on the `chad`
      model are stopped by the shim operator gate.
- [x] C9. Square sandbox tested end to end (`api/test/run-sandbox-e2e.sh`, 32 checks). Found and
      fixed 4 bugs (supachad-landing `1f9ca8e`, branch `fix/square-webhook-hardening` on PR #2):
      duplicate webhooks doubled credits; ANY payment at the location granted premium; amount
      never checked; refunds ignored. Old code fails 11 checks. [U: review + merge, then
      `wrangler d1 migrations apply supachad --remote` + deploy.]
- [x] C10. Test data was local only (throwaway D1/KV, deleted); Square sandbox objects remain in
      the sandbox account (harmless).
- [ ] C12. Signup email + invite automation: send a confirmation and auto-mint (or queue for
      one-click approval) instead of hand-minting. Needs an email sender (Proton is down, D6).
- [U] C3. Switch to Square production (prod token + signature key are already in host creds):
      after PR merge, set SQUARE_ENV=production, prod location id, secrets, and
      production webhook (`/api/square/webhook/prod`) + its signature key. Only after C7–C9 pass.
- [ ] C11. Live smoke test after C3: one real small payment end to end, then refund it [U: your card].
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
- [ ] D6. Proton email is DOWN: the pod's proton-tool-bin sends web-mail@5.0.36, Proton returns
      422 "out of date". The fixed build (`79de3f0215`, PROTON_APP_VERSION override) can't be
      swapped in: OpenShell fingerprints binaries at sandbox creation ("binary integrity check
      failed", confirmed 10-20). Cache now holds the fixed build; it must be in place BEFORE the
      sandbox is created at cutover (verify on chad-next). Then [U] solve Proton's CAPTCHA once.
      doctor now flags it; skill docs no longer tell Chad to rebuild it (`c0dbec6304`).
- [ ] D7. chad-next exits immediately on start (phase Error since the Docker restart) — fix before
      the cutover rehearsal resumes.

## E. Quality + docs

- [~] E1. Upstream merged into chad-dev (`23b6f0510f`, 0 regressions vs baseline+upstream).
      [U] push: the pre-push growth guardrail can't judge a fork merge (needs the user).
- [~] E2. #49 deps: gstack 1.91.27 → 1.91.158 and Smithers 0.32 done; OpenClaw 2026.4.24 → 2026.10.x rides the cutover image; gbrain fork 0.14.2 unchanged.
- [x] E3. #46 Smithers 0.26.1 → 0.32.0: scheduled experiments, mcp-health and fail-report runs
      all green on 0.32 (errors in logs were the wipe window).
- [x] E7. #47 skill review: every path/command in Chad's 5 skills checked against the pod.
      proton-calendar told Chad to rebuild the tool at a path the policy denies → rewritten (D6).
      budget.json was never backed up (wrong path in the backup list) → fixed (`33eb7b756d`).
      The experiments ledger is created on first use and backed up. openwebui skill's 4
      "not yet built" cron wrappers are labelled as suggestions.
- [x] E4. #45 "Agent Runs" (runs.supachad.com) in the OWUI user menu, admins only (in `vm-panel.js`).
- [~] E5. #52 docs refresh: runbooks fixed for the embed model/dims and budget path
      (`72628e503d`). Design docs (MASTER-REVIEW, migration plan) are dated snapshots, left as-is.
- [U] E8. Upstream #5699 retired docs-to-skills and the generated user skills; this fork still
      carries 13 stale generated `nemoclaw-*` skills (already drifting from docs) and a CLAUDE.md
      that tells agents to run the deleted generator (upstream's CLAUDE.md is now a link to
      AGENTS.md). Decide: delete the 13 + adopt upstream's CLAUDE.md, or keep them hand-maintained.
- [U] E6. supachad-docs: allow Actions to create PRs (repo setting).
