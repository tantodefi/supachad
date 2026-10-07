<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# OpenShell 0.0.116 upgrade — credential migration + consequences log

**Goal:** Chad running on OpenShell 0.0.116 with config/files/functionality preserved.
**Status (2026-10-07):** chad healthy on 0.0.36; migration in progress. Backups current
(host tarball `pre-openshell-bump-20261007T071918Z.tar.gz` + GitHub chad-state +
`backups/creds/credentials.json.premerge`).

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

## ⛔ BLOCKED (2026-10-07) — 0.0.116 gateway model is incompatible with our onboard

The destructive recreate was attempted and **stopped before any teardown** when the
blocker surfaced. chad never went down; CLI rolled back to 0.0.36; gateway 0.0.20 still
healthy and manageable.

**Root cause:** OpenShell 0.0.116 changed the gateway deployment model.
- The `openshell gateway` CLI **removed `start`/`destroy`** (0.0.116 has only
  add/remove/login/logout/select/info/list). The local gateway is now a standalone
  `openshell-gateway` gRPC/HTTP **server** (run via the Homebrew service
  `openshell-gateway-homebrew-service` or `brew services`, auto-detecting its
  driver), which you then register with `openshell gateway add <endpoint> --local`.
- Our fork's `bin/lib/onboard.js` still calls `openshell gateway start` (line ~2039)
  and `gateway destroy` (~1633, ~1790). Against the 0.0.116 CLI those fail, so
  `nemoclaw onboard` would abort **mid-teardown** — the worst possible time.
- Secondary: the brew gateway service defaults to name `openshell` / port `17670`,
  while our whole topology (chad-shim, cloudflared tunnel, OpenWebUI) is wired to
  gateway `nemoclaw` / port `8080`. The 0.0.116 deploy must be pinned to that
  name+port (via `openshell-gateway` flags) or the topology breaks.
- Also confirmed en route: the 0.0.116 CLI is Protobuf-wire-incompatible with a
  0.0.20 gateway (`sandbox list` fails), so a half-upgraded CLI cannot manage the
  old gateway — which is why the CLI was rolled back.

**Path forward (a tested dev task, NOT a blind destructive run):**
1. First check whether upstream's `onboard.js` already deploys the gateway via the
   0.0.116 `openshell-gateway` service + `gateway add` (i.e. our merge kept the old
   `gateway start` calls by mistake). If so, this is a merge reconciliation, not new code.
2. Otherwise patch `startGatewayWithOptions`/destroy paths in `bin/lib/onboard.js` to
   launch `openshell-gateway` (driver=docker, `--port 8080`, state dir, pinned
   `OPENSHELL_CLUSTER_IMAGE`=cluster:0.0.116) named `nemoclaw`, then `gateway add`.
3. Dry-run onboard against a THROWAWAY sandbox/gateway first to validate the 0.0.116
   deploy end-to-end before touching chad.
4. Only then: run the staged recreate below + post-onboard restore.

**Staged assets remain valid** (backup, rollback binary, re-provision state, the
onboard command). The blueprint pins were reverted to the live 0.0.32-0.0.36 range so
the running system stays self-consistent until the binary upgrade actually lands.

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
