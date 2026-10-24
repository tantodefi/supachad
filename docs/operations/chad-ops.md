---
title:
  page: "chad-ops: Health Checks and Cold-Start Recovery"
  nav: "chad-ops"
description:
  main: "How to check Chad's health with chad-ops doctor and bring Chad back after a host reboot, pod restart, or cutover rollback."
  agent: "Operator runbook for scripts/chad-ops.sh. Use when the user asks whether Chad is healthy, why Chad is down after a reboot, or how to recover the sandbox, gateway, shim, crons, skills, credentials, or gbrain."
keywords: ["chad-ops", "doctor", "recover", "cold start", "chad sandbox", "recovery runbook"]
topics: ["ai_agents", "operations"]
tags: ["openclaw", "openshell", "chad", "operations", "recovery"]
content:
  type: how_to
  difficulty: technical_intermediate
  audience: ["developer", "engineer", "operator"]
status: published
---

<!--
  SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
  SPDX-License-Identifier: Apache-2.0
-->

# chad-ops: Health Checks and Cold-Start Recovery

`scripts/chad-ops.sh` is the host-side dispatcher for Chad's recurring
operations. It runs on the Mac host (it needs Docker, `kubectl` through the
cluster container, `launchctl`, and SSH to the sandbox) and is never deployed
into the pod. Every step checks current state first and skips healthy work, so
re-running an interrupted recovery finishes it.

## Check Health

```bash
bash scripts/chad-ops.sh doctor
```

`doctor` is read-only. It first confirms the sandbox is reachable; if it is
not, it prints one failure naming what to check (the gateway on `:8080`, the
CLI mTLS certs, the handshake secret) and stops, instead of reading transport
errors as values. In pod mode it also flags a native `openshell-gateway` that
shadows the cluster on `127.0.0.1:8080`. Then it checks:

| Check | Healthy output |
|---|---|
| Shim | `shim :8901 → 200` |
| Gateway | `gateway listening :18789` (container mode: `:1879x`) |
| Inference timeout | `inference timeout 300s` (the 60s default returns HTTP 503 on long replies) |
| Inference | `inference answers (model route OK)`: a real completion through `inference.local`. A failure such as HTTP 404 `Function … Not found for account` means NVIDIA withdrew the configured model; check `openshell inference get` and switch with `openshell inference update --model <id>` |
| gbrain | health score, page count, and embed coverage of 90% or more |
| Binaries | `drift=0  absent=0` against `chad-deploy-manifest.txt` |
| Allowlist gate | `allowlist gate armed (N operators)` |
| Crons | 10 registered |
| Proton | `proton-tool wrapper installed` |
| Proton build | `proton-tool-bin is a current build` (the retired `web-mail@5.0.36` build is rejected by Proton and ships fixed only with a fresh sandbox) |
| GitHub CLI | `gh authenticated (Supachad)` |
| Skills | `skills.load.extraDirs` set |
| OpenWebUI tools | `webui MCP server registered` |
| Long-term memory | `memory-lancedb in memory slot (auto-recall)` |
| gbrain embed config | NVIDIA key and base URL present |
| Agent inbox | fewer than 50 error events today |
| Backups | last `chad-state` commit 12 hours old or newer |

## Recover After a Restart

```bash
bash scripts/chad-ops.sh recover
```

`recover` runs these steps in order, then runs `doctor`:

1. `creds-sync` pushes the filtered host credentials into the pod. It copies
   11 keys: Proton, GitHub, Brave, NVIDIA, Anthropic, the invoker token,
   `OPENWEBUI_API_KEY`, `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`, and
   `CHAD_STATE_REPO`. Without the OpenWebUI and Cloudflare Access keys, every
   `chad-webui` tool fails.
2. `runtime-bins` (container mode only) installs `gh`, `proton-tool-bin`, `bun`,
   and the gbrain library from `~/.nemoclaw/cache/runtime-bins/`, because the
   0.0.116 image ships none of them. `policy-sync` applies any preset from
   `scripts/chad-policy-presets.txt` that is missing. `inference-timeout` sets
   the gateway inference route to 300 seconds.
3. `gh-auth` writes `/sandbox/.config/gh/hosts.yml` from `GITHUB_TOKEN`.
   `gh auth login` rejects the classic PAT because it lacks `read:org`, and the
   OpenClaw exec tool strips `GH_TOKEN` from the environment.
4. `chown`, `gbrain-fix`, and `proton-wrap` restore ownership and the two
   credential wrappers that a restart drops.
5. `gbrain-config` re-writes the embed config that `gbrain init` clobbers.
6. `restore-data` restores workspace state from `chad-state`, but **only** when
   the brain has zero pages or identity is missing. Otherwise it skips, so a
   surviving brain is never replaced by an older snapshot. Set
   `CHAD_FORCE_RESTORE=1` to override. In container mode, `deploy` runs once
   before this step so `chad-restore-from-github` exists.
7. `restore-runtime` restores any **missing** `[runtime]` file from
   `chad-state` (the action-gate policy `auto-actions.json`, `queue/`,
   `memory/feedback-proposals.md`) and the operator `identities/`, without
   touching files that exist. It also copies `workspace/` with no-clobber,
   which brings back `MEMORY.md` and the daily notes under `memory/`.
8. `gbrain-dims` migrates a re-initialized brain from `vector(1536)` to
   `vector(2048)` (the size `nemotron-3-embed-1b` emits). It does nothing when
   the column is already 2048 and refuses when chunks are already embedded.
9. `deploy` pushes every manifest file, including all custom skills and the
   orchestrator tools.
10. `bonjour-off`, `skills-register`, `mcp-register`, and `memory-register`
    fix config that a rewrite drops, then `restart-gateway` loads it.
11. `cron-reload` re-registers the 10 crons with `--no-deliver`. It must run
    after the gateway restart, because `cron add` talks to the gateway.
12. `gate-sync` writes the operator allowlist, restarts the shim, and checks
    that a non-operator is denied and an operator gets a real completion.
13. `chat-prune` deletes brain pages for chats that no longer exist in
    OpenWebUI. The backup never deletes, so a restore brings them back.
14. The recovered marker is written, then `embed-backfill` starts.

## Recover Automatically

The launchd job `dev.nemoclaw.chad-autorecover` runs
`chad-ops.sh autorecover` every 10 minutes. `recover` writes
`/sandbox/.openclaw-data/state/.chad-recovered` once crons and the brain are
back. That file lives on the ephemeral layer, so when it is missing the
sandbox was wiped, and `autorecover` runs `recover`.

When the sandbox is unreachable, `autorecover` first fixes the two causes seen
after a Docker restart: a native gateway shadowing `127.0.0.1:8080`, and a
rotated SSH handshake secret (`handshake-sync`). It waits an hour between
attempts, so a recovery that keeps failing never restarts the gateway every
10 minutes. The plist is tracked in `scripts/openwebui/launchd/`.

## Container Mode

Set `CHAD_SANDBOX_MODE=container CHAD_SANDBOX=<name>` to run any command
against an OpenShell 0.0.116 docker-driver sandbox such as `chad-next`.

:::{warning}
Do not run `nemoclaw <container-sandbox> policy-add` (or other NemoClaw
policy writes) while the pod cluster is running. The CLI starts a native
gateway on `127.0.0.1:8080`, which takes the live pod offline.
`chad-ops policy-sync` refuses in this case.
:::

## Recover After a Host Reboot

A Mac reboot restarts the cluster container and can leave three problems that
`recover` cannot fix on its own. Check them in this order.

### Another gateway owns port 8080

If `openshell sandbox list` prints `No sandboxes found` while the chad pod is
running, check what listens on `:8080`:

```bash
lsof -nP -iTCP:8080 -sTCP:LISTEN
```

An `openshell-gateway` process from `dev.nemoclaw.openshell-gateway` (the
0.0.116 gateway promoted during a cutover attempt) shadows the cluster's
gateway. Unload it and keep it from loading at login:

```bash
launchctl bootout gui/$(id -u)/dev.nemoclaw.openshell-gateway
launchctl disable gui/$(id -u)/dev.nemoclaw.openshell-gateway
```

Re-enable it with `launchctl enable` when the 0.0.116 cutover happens.

### The CLI certs no longer match

`invalid peer certificate: BadSignature` means the host CLI's certs were
re-enrolled against a different gateway. Restore the cluster's own client
bundle, which the command verifies against `:8080` before swapping:

```bash
bash scripts/chad-ops.sh cli-mtls
```

### The SSH handshake secret rotated

If `ssh openshell-chad` fails after a cluster restart, the Helm chart rotated
the handshake secret on the `openshell` StatefulSet while the chad pod kept the
old value:

```bash
bash scripts/chad-ops.sh handshake-sync
```

Then run `recover`.

## Recover a Rebuilt Binary

OpenShell records each allowed binary's SHA-256 on first use and denies a
changed binary afterwards (`binary integrity check failed … hash changed`).
A rebuilt `proton-tool-bin` therefore stays blocked until the sandbox starts
fresh, which the 0.0.116 cutover does. Find the reason with:

```bash
openshell logs chad --since 15m | grep 'action=deny'
```

## Related Docs

- [GBrain in NemoClaw](gbrain.md) covers empty-brain detection, page restore,
  and the `gbrain-dims` schema migration.
- [Chad Devflow Reference](chad-devflow.md) covers `chad-deploy` and the deploy manifest.
- [Chad Skills Catalog](chad-skills.md) covers the custom skills and their policies.
