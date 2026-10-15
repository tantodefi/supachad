<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# OpenShell 0.0.116 upgrade — credential migration + consequences log

**Goal:** Chad running on OpenShell 0.0.116 with config/files/functionality preserved.
**Status (2026-10-13):** latest upstream merged + on `main`; whole migration validated end-to-end on
a parallel `chad-next` container (0.0.116); live chad serving normally on 0.0.20. Remaining =
execute the cutover runbook below (destructive) + the durable gbrain bake. Backups current
(`backups/chad-precutover-20261012T154219Z.tar.gz` + GitHub chad-state +
`backups/creds/credentials.json.premerge`).

---

## ✅ VALIDATED CUTOVER RUNBOOK (2026-10-13) — every step proven on chad-next

This supersedes the earlier dated attempts below. The whole migration was validated end-to-end
against a parallel `chad-next` sandbox (gateway `nemoclaw-8088` on spare port 8088) while live chad
stayed on 0.0.20 — merged upstream code onboards, container ops work, gbrain runs, serving forwards.
Latest upstream is merged + on `main` (commit lineage f3537ddce7 → d340b81bcd). chad's sandbox moves
from a k8s **pod** (cluster:0.0.20) to a native-gateway **docker-driver container** — the cluster
model is gone in 0.0.116.

### Proven primitives (container model)

- **exec (non-root uid 998):** `openshell -g <gw> sandbox exec -n <name> --timeout N -- sh -c '…'`
- **root exec:** `docker exec -u 0 <container> sh -c '…'` (needed for /usr/local/bin writes)
- **file push:** `docker cp src <container>:/tmp/x` → root exec `mv+chown` (bypasses landlock)
- **serving:** `openshell -g <gw> forward service --target-port <p> --local 127.0.0.1:<hp> <name>`
  (gRPC loopback forward; replaces the pod `ssh -L` tunnel) — gateway 18791 forwarded = HTTP 200
- container name = `openshell-default--<sandbox>-<uuid>` (`docker ps | grep`)

### Cutover sequence (destructive — live chad down during it; rollback = restart the 0.0.20 cluster)

1. **Fresh backup** of live chad `/sandbox` (gbrain + .openclaw + .openclaw-data + .nemoclaw) via
   `docker exec cluster kubectl exec chad -- tar … | gzip` (see `backups/chad-precutover-*.tar.gz`).
   Also tar out the gbrain+bun RUNTIME: `tar c /usr/local/lib/gbrain /usr/local/bin/bun`.
2. **Quiet watchdogs:** `launchctl bootout` chad-gateway/shim/tunnel-watchdog.
3. **Free 8080:** `docker stop openshell-cluster-nemoclaw` (live chad goes down here).
4. **Promote gateway:** `launchctl enable gui/$(id -u)/dev.nemoclaw.openshell-gateway` (it is
   disabled while chad is on 0.0.20 — see rollback), then
   `launchctl bootstrap … dev.nemoclaw.openshell-gateway` (native 0.0.116 on
   8080, `--drivers docker`, `OPENSHELL_LOCAL_TLS_DIR` with jwt/ → logs "mTLS … enabled" +
   "gateway-minted sandbox JWT enabled"). Re-enroll CLI: `openshell gateway add https://127.0.0.1:8080
   --name nemoclaw --local`.
5. **Clear stale registry** (else onboard throws "predates native NVIDIA provider attachments"):
   remove any `chad` entry from `~/.nemoclaw/gateways/8080/sandboxes.json` + `onboard-session.json`.
6. **Onboard** (merged tree): `NVIDIA_API_KEY=… NEMOCLAW_YES=1 NEMOCLAW_PROVIDER=build
   NEMOCLAW_MODEL=nvidia/nemotron-3-super-120b-a12b NEMOCLAW_IGNORE_RUNTIME_RESOURCES=1
   NEMOCLAW_OPENSHELL_BIN=/opt/homebrew/opt/openshell/bin/openshell nemoclaw onboard
   --non-interactive --yes --yes-i-accept-third-party-software --recreate-sandbox --name chad
   --from /Users/r/.nemoclaw/source/Dockerfile` → expect [1/8]..[8/8] "Deployment verified"
   (the #38 convergence-respawn in nemoclaw-start.sh makes [7/8] pass).
7. **Deploy custom layer:** `CHAD_DEPLOY_VIA=container CHAD_SANDBOX=chad chad-deploy.sh --push`.
8. **gbrain runtime:** docker cp the gbrain+bun tar into the container, extract as root (durable TODO:
   bake a `bun install github:tantodefi/gbrain` + patches layer into the chad Dockerfile).
9. **Recover:** `CHAD_SANDBOX_MODE=container CHAD_SANDBOX=chad chad-ops.sh recover` (chown, gbrain-fix,
   gate-sync, cron-reload, skills-register, gbrain-config, creds-sync).
10. **Restore memory:** docker cp chad's `brain.pglite` + apply the **halfvec/2048 schema fix**
    (NOT a naive `gbrain init` — fresh init re-runs `CREATE INDEX hnsw` on vector(2048) → FAILS; see
    [[project_gbrain_embed_stack]]); embed-backfill.
11. **Serving:** ensure chad-shim running in-container (8901); `forward service --target-port 8901
    --local 127.0.0.1:8901 chad` (persistent via launchd); point OWUI/nvidia-proxy at host:8901.
    Retire the SSH-tunnel + pod-gateway watchdogs (container self-supervises the gateway).
12. **Verify:** OWUI 200, chad responds, gbrain health, operator-allowlist gate denies/allows.
    **Rollback:** `launchctl bootout` **and `launchctl disable`** the 0.0.116 gateway →
    `docker start openshell-cluster-nemoclaw` → `chad-ops cli-mtls` (the step-4 re-enroll overwrote
    the CLI certs) → reload watchdogs; chad back on 0.0.20 in ~1–2 min (the cluster is stopped, not
    deleted). Attempt 1 only booted the gateway out: its plist is `RunAtLoad`, so the next login
    relaunched it on `127.0.0.1:8080` over the cluster's `*:8080` and chad stayed down after a reboot.

### Cutover attempt 1 (2026-10-14) — what it proved and what changed since

Attempt 1 ran steps 1–10 on real chad (`ONBOARD_EXIT=0`, "Deployment verified"), then
`chad-ops recover` killed the container (exit 137) and it rolled back. Fixed since, on `main`:

- `creds-sync` streams over `docker exec -i` in container mode; `restart-gateway` / `restart-shim`
  no longer `pkill` the container's main process (`b5443809be`; full recover rehearsed on chad-next).
- `creds-sync` now pushes 11 keys (adds `OPENWEBUI_API_KEY`, `CF_ACCESS_*`, `CHAD_STATE_REPO`).
- `restore-data` skips when the brain has pages, so step 9 can't overwrite step 10's restore; use
  `CHAD_FORCE_RESTORE=1` only when the container really is empty.
- New steps run by `recover`: `gh-auth`, `proton-wrap`. `cron-reload` fetches `jobs.json` from
  chad-state when missing.
- **Step 10 is now `chad-ops gbrain-dims`** (vector(2048), no hnsw) after importing `brain/` from
  chad-state — see `docs/operations/gbrain.md`. A fresh init creates vector(1536)+hnsw.
- **A fresh container is also the fix for Proton**: OpenShell's binary-integrity cache pinned the old
  `proton-tool` hash on 0.0.20, so the rebuilt binary (app version `web-mail@5.0.136.4`, CAPTCHA
  handoff) only works in a sandbox that sees it first. Expect one CAPTCHA URL from
  `proton-tool whoami` after the cutover; solve it, re-run once.

Runbook for host-reboot recovery on 0.0.20 (until the cutover): `docs/operations/chad-ops.md`.

### Still TODO before/around the cutover

- Durable gbrain+bun Dockerfile layer (reviewed-install conventions) — step 8 is a runtime copy today.
- Container-adapt chad-setup.sh gbrain runtime + the watchdogs (tunnel/gateway ones are obsoleted).
- #49 beyond-upstream bumps (OpenClaw 2026.9.2→2026.10.2, mcporter 0.7.3→0.14.3) are SEPARATE
  (upstream still pins the old versions) — not required for the cutover.

---

## Why this is a migration, not a bump

The 0.0.116 transactional rebuild (`nemoclaw chad rebuild` / `backup-all`) **scans the
whole `/sandbox` native state and rejects any credential-bearing file** (verified: it
flagged `credentials.json` even after moving it). Chad keeps its 7 operational creds in
`/sandbox/.nemoclaw/credentials.json`, read by ~15 scripts. So the file must go, and the
creds must live in OpenShell's **gateway credential vault**.

## The vault mechanism (verified)

- Register: `openshell provider create --name <n> --type <type> --credential <ENV_NAME>`
  (reads the value from the host env at creation; nothing persisted to host disk).
- **Generic-type creds ARE injected into the sandbox process env** — proven: `BRAVE_API_KEY`
  is live in the sandbox env because it's registered as `chad-brave-search` (type generic).
- Provider-type creds (nvidia/anthropic) are used by the inference gateway, **not** env-injected.
- Already in the vault: `nvidia-prod` (nvidia), `chad-brave-search` (generic).

## Credential inventory (chad's 7 pod creds)

| Cred (file key) | Used for | Target | Env-injected? | Status |
|---|---|---|---|---|
| `NVIDIA_API_KEY` | inference + chad scripts (refresh-models, gbrain embed) | provider `nvidia-prod` **+** generic for scripts | generic: yes | partial (provider done) |
| `BRAVE_API_KEY` | web search | generic `chad-brave-search` | yes | ✅ done |
| `ANTHROPIC_API_KEY` | claude subagents | provider (anthropic) + generic if scripts need it | — | todo |
| `GITHUB_TOKEN` | backup/restore/clone/PRs | generic `chad-github` | yes | todo |
| `PROTON_USERNAME` | email | generic `chad-proton-user` | yes | todo |
| `PROTON_PASSWORD` | email | generic `chad-proton-pass` | yes | todo |
| `NEMOCLAW_INVOKER_TOKEN` | invoker auth | generic `chad-invoker` | yes | todo |

## Scripts that read `credentials.json` → must rewire to env

`chad-backup-to-github.sh`, `chad-restore-from-github.sh`, `chad-clone-source.sh`,
`chad-setup.sh`, `chad-ops.sh`, `openwebui/chad-shim.py`, `openwebui/chad-shim-watchdog.sh`,
`openwebui/chad-webui`, `chad-cron-wrappers/{chad-auth-context,chad-premium-client,_chad-paths.sh}`,
`chad-smithers/{chad-moshi-notify,refresh-models.js}` (+ `chad-workspace-files.txt`,
READMEs). Because the vault injects each generic cred under its **own key name**
(`GITHUB_TOKEN` → env `GITHUB_TOKEN`), the rewire is mechanical: read `$GITHUB_TOKEN`
(env) instead of `credentials.json['GITHUB_TOKEN']`. A tiny `chad-cred` helper (env-first,
file-fallback during transition) keeps everything working until the file is removed.

## ⚠️ Scope finding (2026-10-07) — this is a PLATFORM upgrade, not a pin flip

Two discoveries change phase 6 from "`nemoclaw chad rebuild`" into a bigger operation:

1. **Version skew requires a host/cluster upgrade first.** The blueprint comment is
   explicit: *"bump these to 0.0.116 AFTER upgrading the pod's OpenShell binary."* The
   host `openshell` CLI is **0.0.36**; the pod runs the OpenShell binary the 0.0.36
   host/cluster provisions. Getting **0.0.116 into the pod requires upgrading the host
   OpenShell CLI (0.0.36 → 0.0.116) and its cluster**, then recreating the pod — *then*
   flipping the pins. This is a host-platform upgrade, with its own risk surface, not a
   sandbox-only change.
2. **The rebuild scanner surface spans many state files, not just `credentials.json`.**
   A content scan of `/sandbox` shows secret-bearing files across the tree:
   `.gbrain/config.json` (holds the NVIDIA key), `.openclaw/openclaw.json` (device/gateway
   creds), chad-premium preset, and session trajectories (may contain secrets Chad
   handled). The **transactional** (export-based) rebuild would reject these one by one —
   whack-a-mole. The clean path is a **fresh pod recreate + selective restore from our own
   backup** (the cold-start runbook), which never runs the export scanner. That path
   depends on the host/cluster already being 0.0.116 (point 1).

**Revised recommended mechanism for phase 6:** upgrade host CLI + cluster to 0.0.116 →
recreate the chad pod on the new platform (gets OpenShell 0.0.116) → restore gbrain +
non-cred state from our backup (NOT the cred file) → vault env-injects the 7 creds →
`chad-ops recover`. The transactional `nemoclaw chad rebuild` is the wrong tool here.

**Also note:** kubectl in the current host shell is pointing at the default
`localhost:8080` (TLS/plaintext mismatch) — the openshell cluster context must be
selected before any pod operation.

## 🔬 [7/8] DEEP-DIVE (2026-10-12) — staging harness; blocker is the docker-driver forward

Investigated onboard [7/8] on a **staging harness so chad is never touched** (chad stays on
0.0.20): a second 0.0.116 gateway `nemoclaw-8081`→`nemoclaw-18080` on a spare port, driven by
`NEMOCLAW_GATEWAY_PORT=18080` + `NEMOCLAW_OPENSHELL_BIN=/opt/homebrew/opt/openshell/bin/openshell`

+ a throwaway `stage` sandbox. Reusable; chad's gateway/CLI untouched throughout.

**What we proved:**

- The OpenClaw 2026.9.2 agent gateway **does reach ready inside the sandbox** — a converge
  pass logged `[gateway] ready`, 14 plugins, and `device pairing auto-approved role=operator`.
- First-run it applies state migrations and, with `gateway.reload=off` (#4710), **refuses ready
  + exits** until restarted. A first-run converge pass (run `gateway run` once, stop on ready so
  migrations persist) resolves that specific refusal.
- **BUT even with the in-sandbox gateway ready, onboard (on the HOST) still gets
  `ws://127.0.0.1:18789 ECONNREFUSED`.** The real wall is the **host→sandbox port-forward** for
  the dashboard/gateway port under the 0.0.116 docker driver — the same failure class as attempt
  1's "Cannot allocate dashboard port: could not prove OpenShell forward ownership." The gateway
  is fine; onboard can't *reach* it.

**Tried and reverted (none fixed [7/8], all working-tree only, now reverted):** settlement-metadata
relax (wrong branch — reverted), plugin pre-install (plugin is already baked; `plugins install
/opt/nemoclaw` fails — no package.json), first-run converge pass in nemoclaw-start (loopback +
kill-on-ready — does converge the migration but doesn't fix the forward), `gateway.bind:loopback`
(WRONG: loopback isn't forward-reachable from the host → guarantees ECONNREFUSED), `bind:auto`.

**Conclusion / next:** the remaining blocker is onboard's **docker-driver host↔sandbox port
forward** (host 18789 → sandbox 18789), not the agent gateway. Fixing it needs work in onboard's
docker-driver forward path (and likely NemoClaw/OpenShell upstream guidance for the 0.0.116 local
docker-driver model, which is still maturing — see NVIDIA/OpenShell#1314). The converge-pass idea
is sound and can be re-applied once the forward works. The only committed change from this whole
effort is the mcporter security bump (96927d3200); everything else is reverted — chad is safe and
serving on 0.0.20.

## ✅ CUTOVER ATTEMPT 3 (2026-10-08) — OpenShell layer SOLVED; onboard reaches [7/8]

The settlement blocker is cracked — the root cause was NOT settlement-code strictness (that
patch was wrong + reverted). It was **the gateway deployed without sandbox-JWT auth**. The
0.0.116 docker driver requires gateway-minted sandbox JWT; without it, sandboxes are created
("reach Ready") but never register → `sandbox list` is empty → settlement times out.
Confirmed via SETTLE_DIAG instrumentation (selector AND no-selector list both `[]`).

**Gateway deployment recipe that works (host config, no code change):**

- launchd plist runs `openshell-gateway` **directly** (NOT the `…-homebrew-service` bash
  wrapper — onboard's process-identity preflight must see the real binary with `--name`):
  `openshell-gateway --name nemoclaw --bind-address 127.0.0.1 --port 8080 --health-port 8090
  --tls-cert/key/client-ca <LTD>/… --drivers docker`
- with env **`OPENSHELL_LOCAL_TLS_DIR=/Users/r/.local/state/openshell/homebrew/tls`** — that
  dir has `jwt/signing.pem` (→ "gateway-minted sandbox JWT enabled") and lives under $HOME so
  Docker Desktop can bind-mount it into sandbox containers. (The homebrew-service launcher
  populates that dir; we reuse it but invoke the binary directly.)
- `~/.config/openshell/gateway.env`: `OPENSHELL_GATEWAY_NAME=nemoclaw`, `OPENSHELL_SERVER_PORT=8080`,
  `OPENSHELL_HEALTH_PORT=8090`, `OPENSHELL_DRIVERS=docker`.
- Gateway log must show BOTH `mTLS user authentication enabled` AND `gateway-minted sandbox
  JWT enabled`. Both present ⇒ onboard reaches **`✓ Sandbox 'chad' created`** and settles.

**Remaining blocker (task #38) — OpenClaw AGENT gateway, a different layer:** onboard [7/8]
"Setting up OpenClaw inside sandbox" fails — the OpenClaw agent gateway inside the sandbox is
unreachable (`ws://127.0.0.1:18789` ECONNREFUSED on restart), sandbox → phase Error. Prime
suspect: bonjour/mDNS plugin crashing the OpenClaw gateway (known chad gotcha) and/or the
sandbox-writable `.openclaw/{devices,workspace/state,identity,cron}` subpaths. Fix likely =
disable bonjour in the sandbox image's default openclaw.json (Dockerfile) / ensure subpaths,
so [7/8] gateway restart succeeds → [8/8] → state restore → chad-ops recover → verify.

Also resolved: the k8s route is the WRONG path for chad (OpenShell docs: Docker is the
supported local/single-machine driver; the k8s path is experimental + needs the Agent Sandbox
CRDs + a NetworkPolicy-enforcing CNI that Docker Desktop's kind lacks). Docker Desktop k8s was
enabled during investigation but isn't needed; can be turned back off.

## ⛔ CUTOVER ATTEMPT 2 (2026-10-08) — reached sandbox-Ready; blocked on docker-driver settlement

Got MUCH further than attempt 1. chad restored + serving on 0.0.20 after rollback.
Sequence that now WORKS end-to-end: activate 0.0.116 CLI → `docker stop` old gateway →
launchd `openshell-gateway` (nemoclaw:8080, docker driver) → `gateway add/select` (brew
certs match) → `nemoclaw onboard --fresh --from Dockerfile` → **image builds clean** →
**sandbox 'chad' reaches Ready**.

**Two real blockers resolved along the way:**

1. ✅ **mcporter audit gate** (the real attempt-1 "build failure", NOT memory): the
   sandbox-base build's `verify-mcporter-audit.sh` blocked on 2 high+ advisories
   (GHSA-jqcg-44mw-7w3h critical proxy-addr, GHSA-6qxp-vccf-f47h high MCP SDK). Fixed by
   bumping mcporter-runtime proxy-addr 2.0.7→2.0.8 + @modelcontextprotocol/sdk
   1.29.0→1.32.1 (non-breaking, lockfile-only, commit 96927d3200). Build now clean.
   **The Docker memory bump was never needed** (settings-store.json edits are reverted by
   Docker Desktop anyway; build succeeds on 7.75 GiB).
2. ✅ **Docker/registry hygiene**: cleared stale forward pid, pruned stale sandbox
   registry entries (test-sb/my-sb + old 0.0.20 chad), provider key is `build` (not
   `nvidia`) for NVIDIA-hosted inference (integrate.api.nvidia.com/v1).

**THE REMAINING BLOCKER — `settlement-incomplete` (reproducible):** after the sandbox
reaches Ready, onboard's finalization (`src/lib/onboard/created-sandbox-finalization.ts`,
`sandbox-readiness-tracing.ts`) requires OpenShell to return "one exact durable sandbox
identity" for the create. The 0.0.116 **docker-driver** gateway does NOT return it
(`identityWasUnavailable: true`) → onboard errors + writes a retained-recovery record.
No bypass env/flag exists. Likely the finalization assumes **kubernetes-driver** behavior
(the old 0.0.20 gateway was k3s). Each failed attempt leaves
`~/.nemoclaw/retained-sandbox-recovery.json` which then blocks destroy AND onboard
(catch-22); cleared manually after verifying the sandbox is truly absent (gateway empty,
no container, identity null = nothing durably created).

**Fix paths for next session (pick one):**

- (a) Patch onboard finalization to accept a docker-driver sandbox that reached Ready
  without the durable-identity response (treat Ready as success when driver=docker), OR
- (b) deploy the 0.0.116 gateway with `--drivers kubernetes` against a k3s (match the old
  model so the identity IS returned), OR
- (c) determine the gateway/driver config that makes the docker driver return the durable
  identity. Then re-run the proven sequence above → restore state → recover → verify.

## ✅ VALIDATED (2026-10-07) — initial "blocker" was DEAD CODE; cutover path proven

**Correction to the earlier blocked note:** the `openshell gateway start`/`destroy`
calls that looked like a blocker live in `bin/lib/onboard.js`, which is an **orphaned
legacy file** — nothing in the active runtime requires it. The real `nemoclaw onboard`
is the TS-migrated `dist/lib/onboard.js`, which is **version-aware**: it checks
`supportsLifecycle(...)` and, on macOS, `startGatewayWithOptions` (src/lib/onboard/
gateway/start.ts:103-142) **reuses an already-running gateway** rather than starting it
(the Linux-docker-driver auto-start branch is skipped on macOS). So onboard will NOT
fail on `gateway start`.

**The real 0.0.116 model (confirmed on this Mac):** the gateway is the native
`openshell-gateway` gRPC/HTTP server, configured by flags / `gateway.toml` / env, TLS at
`/opt/homebrew/var/openshell/tls/`, using a **docker compute driver** that pulls
`ghcr.io/nvidia/openshell/supervisor:0.0.116` and manages sandbox containers directly
(no more `cluster:x` container). You register the CLI to it with
`openshell gateway add <endpoint> --local`, then `nemoclaw onboard` reuses it.

**End-to-end validated NON-destructively** (throwaway gateway on port 18080, temp state,
docker driver — torn down after; chad untouched throughout):

- 0.0.116 `openshell-gateway` starts healthy on this Mac (TLS/mTLS, docker driver,
  supervisor:0.0.116 pulled, workspace created, `healthz` 200).
- 0.0.116 CLI → 0.0.116 gateway: `gateway add` ✓, `sandbox list` ✓ (**no protobuf skew**).
- Confirmed isolatable: old `openshell-cluster-nemoclaw` + chad sandbox + OpenWebUI all
  stayed up the entire time.

### CUTOVER RUNBOOK (destructive — chad down during it; every component pre-validated)

1. Re-activate 0.0.116 CLI: point `~/.local/bin/openshell` back at `/opt/homebrew/bin/openshell`.
2. **Persistent gateway service**: configure `openshell-gateway` as a durable service
   (launchd via `brew services`, or a `dev.nemoclaw.openshell-gateway.plist`) with
   `--name nemoclaw --port 8080 --bind-address 127.0.0.1 --drivers docker` + the TLS
   certs, so it survives reboots like the old docker gateway did. Write the config to
   `~/.config/openshell/gateway.{toml,env}`.
3. Stop the old 0.0.20 gateway (`docker stop openshell-cluster-nemoclaw`) and start the
   0.0.116 service; `openshell gateway add https://127.0.0.1:8080 --local --name nemoclaw`.
4. `nemoclaw onboard --from Dockerfile ...` (env-driven, see staged command below) —
   reuses the running gateway, builds the 2026.9.2 image, creates the chad sandbox.
5. Re-provision vault creds + inference; restore non-cred state from the backup; redeploy
   env-first scripts; `chad-ops recover`; verify (doctor, gbrain, OWUI, crons).
   Re-flip blueprint pins to 0.0.116 once the sandbox is live on it.

**Remaining pre-cutover item:** finalize + test the persistent gateway service config
(step 2) — a launchd plist wrapping `openshell-gateway` for nemoclaw:8080, so the
always-on gateway survives reboots. All other components are proven. Staged assets
(backup, rollback binary, re-provision state) remain valid.

## ✅ Platform upgrade executed (2026-10-07) — reversible work done; one destructive step staged

**Done + verified (all reversible):**

- Host `openshell` CLI upgraded **0.0.36 → 0.0.116** (Homebrew tap `nvidia/openshell`,
  `scripts/install-openshell.sh`; our fork pins OpenShell to exactly 0.0.116).
  Old binary saved at `backups/openshell-0.0.36-rollback` for rollback.
- `~/.local/bin/openshell` repointed to the 0.0.116 brew binary; `openshell gateway
  select nemoclaw` done.
- Blueprint pins flipped 0.0.32–0.0.36 → **0.0.116** (`nemoclaw-blueprint/blueprint.yaml`).
- Complete verified state backup: `backups/chad-full-preupgrade-20261007T154729Z.tar.gz`
  (28M; .gbrain + .openclaw [minus regenerable plugin-runtime-deps] + .openclaw-data +
  .nemoclaw; all critical files present).
- Re-provision state captured: `backups/provider-list-preupgrade.txt`,
  `inference-preupgrade.txt`, `gateway-info-preupgrade.txt`. 7 creds in
  `backups/creds/credentials.json.premerge`.

**Key facts discovered:**

- The 0.0.116 CLI is **wire-incompatible** with the running 0.0.20 gateway
  (`sandbox list` → Protobuf decode error). chad's pod still serves users
  independently, but the host CLI cannot manage it until the gateway is recreated.
- chad's sandbox is a **custom image built from the nemoclaw source `Dockerfile`**
  (`onboard --from`; registry `imageTag: openshell/sandbox-from:…`), NOT the stock
  pinned digest. The source Dockerfile already targets **OpenClaw 2026.9.2**.
- `nemoclaw onboard` rewrites the source `Dockerfile` in place (ARG substitution) and
  is driven by env: `NEMOCLAW_PROVIDER`, `NEMOCLAW_MODEL`, `NVIDIA_API_KEY`,
  `NEMOCLAW_ENDPOINT_URL`, `NEMOCLAW_SANDBOX_NAME`, `NEMOCLAW_RECREATE_SANDBOX=1`,
  `NEMOCLAW_POLICY_MODE`/`NEMOCLAW_POLICY_PRESETS`.
- The lifecycle registry (`~/.nemoclaw/sandboxes.json`) entry for chad is incomplete
  (missing openshellVersion/gatewayName/etc.) — doctor says "re-register or re-onboard
  before lifecycle commands." The saved `onboard-session.json` is stale (all pending).

### THE DESTRUCTIVE STEP (run to finish the upgrade) — takes live chad down

```bash
cd /Users/r/.nemoclaw/source
# NVIDIA_API_KEY from backups/creds/credentials.json.premerge
NEMOCLAW_PROVIDER=nvidia \
NEMOCLAW_MODEL=nvidia/nemotron-3-super-120b-a12b \
NVIDIA_API_KEY="$(python3 -c "import json;print(json.load(open('/Users/r/.nemoclaw/backups/creds/credentials.json.premerge'))['NVIDIA_API_KEY'])")" \
NEMOCLAW_SANDBOX_NAME=chad \
NEMOCLAW_RECREATE_SANDBOX=1 \
nemoclaw onboard --from Dockerfile --agent openclaw --sandbox-gpu \
  --non-interactive --yes --yes-i-accept-third-party-software
```

This rebuilds the 2026.9.2 image, recreates the gateway at 0.0.116, and recreates the
chad sandbox. **Note:** onboard creates a *fresh* openclaw.json — chad's real config,
policies, cron, identity, gbrain are restored from the backup in the NEXT step.

### POST-ONBOARD (Claude completes these — no human needed)

1. Re-register vault creds + inference (gateway recreate wipes them):
   `openshell inference set --provider nvidia-prod --model nvidia/nemotron-3-super-120b-a12b`
   then the 8 `openshell provider create` registrations (see provider-list-preupgrade.txt;
   values from credentials.json.premerge) — **but NOT a /sandbox/.nemoclaw/credentials.json**.
2. Restore non-cred state from `chad-full-preupgrade-*.tar.gz`: `.gbrain`, `.openclaw`
   (openclaw.json, identity, devices, cron, workspace, agents/sessions), `.openclaw-data`,
   `.nemoclaw/blueprints` — NOT `credentials.json` (migrated to the vault).
3. `chad-deploy.sh --push` (redeploy env-first scripts) + `chad-ops recover`
   (gateway/shim/tunnel/cron).
4. Verify: `nemoclaw chad doctor`, gbrain search, OpenWebUI round-trip, crons, backup/restore.

### Rollback (if onboard fails badly, before re-provisioning)

`cp backups/openshell-0.0.36-rollback ~/.local/bin/openshell` (break the symlink first),
redeploy the 0.0.20 gateway, restore the backup. chad returns to the pre-upgrade state.

## Migration plan (phased, reversible until step 6)

1. **Register** the operational creds as generic vault entries (env-injected). *Additive.*
2. **Rewire** chad's scripts to read from env (via a `chad-cred` helper, env-first).
3. **Update `chad-ops creds-sync`** to register vault creds instead of writing the file.
4. **Remove** `/sandbox/.nemoclaw/credentials.json`; verify `nemoclaw backup-all` passes (scanner clean).
5. **Flip blueprint pins** 0.0.32–0.0.36 → 0.0.116.
6. **Rebuild** chad transactionally to 2026.9.2 under 0.0.116 (keeps a recovery).
7. **Restore** gbrain + non-cred state; `chad-ops recover` for gateway/shim/tunnel/cron.
8. **Verify** functionality (doctor, gbrain search, OpenWebUI, crons, backup/restore).

## Progress

- ✅ **Phase 1 (2026-10-07):** registered 6 generic vault creds — `chad-github`,
  `chad-proton-user`, `chad-proton-pass`, `chad-invoker`, `chad-anthropic`,
  `chad-nvidia-scripts` — alongside the pre-existing `nvidia-prod` + `chad-brave-search`.
  All 7 operational creds are now in the gateway vault; they env-inject on next
  sandbox start (additive + reversible via `openshell provider delete`).

## CONSEQUENCES LOG (what broke / needs rewiring / needs re-provisioning)

- ✅ **Good news — scripts were already env-first.** `chad-backup/restore/clone`
  (GITHUB_TOKEN), `chad-premium-client` (ANTHROPIC), `chad-moshi-notify` (MOSHI) all
  use env before the file. Since the vault now injects those env vars, they work
  without the file. **No mass rewire needed.**
- ✅ **Fixed:** `chad-auth-context` now reads `NEMOCLAW_INVOKER_TOKEN` env-first
  (commit 190561609f).
- ✅ host-side + env-first, unaffected by pod file removal: `chad-shim.py`
  (CHAD_OPERATOR_ALLOWLIST from host env), `chad-smithers/*` (host creds).
- ⚠️ **TODO — `chad-ops creds-sync` rewrites the pod file** (`cmd_creds_sync`,
  chad-ops.sh:159). It MUST stop writing `/sandbox/.nemoclaw/credentials.json` (that
  re-introduces the scanner blocker) and instead register/refresh vault providers.
  **Blocking:** until fixed, the next creds-sync undoes the migration.
- ⚠️ **TODO — `chad-webui` is deployed IN the sandbox** (`/sandbox/.openclaw-data/bin/chad-webui`)
  and reads creds from the file — notably it needs `OPENWEBUI_API_KEY`, which is NOT one
  of the 7 pod creds today. If chad-webui is used in-sandbox, `OPENWEBUI_API_KEY` must be
  added to the vault (generic) too. Verify its exact key usage.
- ⚠️ **OPEN for the rebuild — other credential files under `/sandbox`.** The scanner
  rejects ANY credential-bearing file, not just `credentials.json`. `.openclaw-data`
  likely holds channel creds (whatsapp/proton session, gateway tokens). The transactional
  rebuild's export may flag these too. **Must test `backup-all`/rebuild export after the
  main file is gone and handle each flagged file** (move its cred into the vault, or
  confirm it's regenerated, not archived).
