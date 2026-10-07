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
