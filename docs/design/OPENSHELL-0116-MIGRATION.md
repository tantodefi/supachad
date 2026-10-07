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

## Migration plan (phased, reversible until step 6)
1. **Register** the operational creds as generic vault entries (env-injected). *Additive.*
2. **Rewire** chad's scripts to read from env (via a `chad-cred` helper, env-first).
3. **Update `chad-ops creds-sync`** to register vault creds instead of writing the file.
4. **Remove** `/sandbox/.nemoclaw/credentials.json`; verify `nemoclaw backup-all` passes (scanner clean).
5. **Flip blueprint pins** 0.0.32–0.0.36 → 0.0.116.
6. **Rebuild** chad transactionally to 2026.9.2 under 0.0.116 (keeps a recovery).
7. **Restore** gbrain + non-cred state; `chad-ops recover` for gateway/shim/tunnel/cron.
8. **Verify** functionality (doctor, gbrain search, OpenWebUI, crons, backup/restore).

## CONSEQUENCES LOG (what broke / needs rewiring / needs re-provisioning)
_Populated as we execute._

- [pending] Host `chad-ops creds-sync` currently pushes the file → must become vault registration.
- [pending] ~15 in-sandbox scripts read `credentials.json` → env rewire (via `chad-cred` helper).
- [pending] The chad **shim** (host-side `chad-shim.py`) reads `CHAD_OPERATOR_ALLOWLIST` from the pod creds file — confirm whether it's host-side (host creds) or needs the vault.
- [pending] `NVIDIA_API_KEY` is both a provider (inference) AND read by scripts → register generic too.
- [open question] Does the rebuild preserve `.openclaw-data` (channel creds, cron, identity), or do those also trip the scanner? Test `backup-all` after the file is gone.
