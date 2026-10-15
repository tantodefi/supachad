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
errors as values. Then it checks:

| Check | Healthy output |
|---|---|
| Shim | `shim :8901 → 200` |
| Gateway | `gateway listening :18789` |
| gbrain | health score, page count, and embed coverage of 90% or more |
| Binaries | `drift=0  absent=0` against `chad-deploy-manifest.txt` |
| Allowlist gate | `allowlist gate armed (N operators)` |
| Crons | 10 registered |
| Proton | `proton-tool wrapper installed` |
| GitHub CLI | `gh authenticated (Supachad)` |
| Skills | `skills.load.extraDirs` set |
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
2. `gh-auth` writes `/sandbox/.config/gh/hosts.yml` from `GITHUB_TOKEN`.
   `gh auth login` rejects the classic PAT because it lacks `read:org`, and the
   OpenClaw exec tool strips `GH_TOKEN` from the environment.
3. `chown`, `gbrain-fix`, and `proton-wrap` restore ownership and the two
   credential wrappers that a restart drops.
4. `gbrain-config` re-writes the embed config that `gbrain init` clobbers.
5. `restore-data` restores workspace state from `chad-state`, but **only** when
   the brain has zero pages or identity is missing. Otherwise it skips, so a
   surviving brain is never replaced by an older snapshot. Set
   `CHAD_FORCE_RESTORE=1` to override.
6. `deploy` pushes every manifest file, including all five custom skills.
7. `cron-reload` re-registers the 10 crons. If `jobs.json` is missing, it
   fetches `cron/jobs.json` from `chad-state` first.
8. `bonjour-off`, `skills-register`, `restart-gateway`, `gate-sync`, and
   `embed-backfill` finish the bring-up.

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
