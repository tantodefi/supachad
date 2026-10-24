<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Chad master todo

Single list of everything open on Chad/supachad, consolidated 2026-10-24.
Status: `[ ]` open (Claude) · `[~]` in progress · `[U]` needs the user · `[x]` done.
Finished work is in "Done" at the bottom and in git history.

## 1. Needs the user (blocking, in order)

- [U] U1. Push `chad-dev` (161 commits ahead of origin, incl. the 10-19 and 10-24 upstream merges) and
      the tags `pin/pi-qualification-2be64a15` and `pin/pi-qualification-798f8f2c` (new pinned
      revision from upstream; repo checks need it reachable). The pre-push growth guardrail can't judge a fork merge;
      Claude is not allowed to skip it. Blocks: CI on chad-dev, and the weekly upstream sync
      (it builds on `origin/chad-dev` and runs the pushed version of its own fixes).
      `git push --no-verify origin chad-dev` ·
      `git push --no-verify origin 2be64a15ee2e3afd769b7a5dacccab893eba3aed:refs/tags/pin/pi-qualification-2be64a15`
      `git push --no-verify origin pin/pi-qualification-798f8f2c`
- [U] U2. supachad-landing PR #7 (premium = $15 CAD/month subscription): merge, then
      `git pull && cd api && wrangler deploy`. Claude then adds subscription events to the
      sandbox webhook and runs a live sandbox subscription.
- [U] U3. Run `~/fix-ff-mislabel.sh` (deletes the 313 mislabeled "Starting Strength" pages).
- [U] U4. supachad-docs PR #2 (daily docs sync, open since 10-15): review + merge; its preview
      deploy waits on "Approve and run" every day because a bot opened the PR.
- [U] U5. Production cutover #44, with you present. Bundle: raise Docker VM memory (8 GB,
      shared with WordPress/OWUI; a Docker restart wipes the pod anyway); Proton CAPTCHA once
      (email is down until then — U5 is the only fix); fixed proton-tool-bin must be in the
      image before the sandbox is created.
- [U] U6. Square production: after U2 — `api/scripts/square-subscription-plan.sh production`,
      prod `SQUARE_SUBSCRIPTION_PLAN_ID` + location id, `SQUARE_ENV=production`, secrets;
      prod webhook with payment.*, refund.*, subscription.created/updated,
      invoice.payment_made. Then one real small payment + refund (your card). Then point the
      sandbox webhook away from the live Worker.
- [U] U7. Signup email: pick an email sender for the Worker (Resend / Postmark / CF Email) —
      Proton is down and isn't a transactional sender. Unblocks C12.
- [U] U8. Cloudflare API token with Access:Edit (`CF_ACCESS_READY=1`) so paid/invited users are
      let through Cloudflare Access automatically.
- [U] U9. Decisions: (a) retire or enable the shadow Smithers workflows (bug-report files
      issues, self-improve tunes crons, skill-improve files enhancements) — today they only
      log "would do"; (b) E8: delete the 13 stale generated `nemoclaw-*` skills + adopt
      upstream's CLAUDE.md; (c) D5 chad-state repo owner (tantodefi vs Supachad);
      (d) tjcooke's OpenWebUI API key.

## 2. Claude — now (in order)

- [ ] N2. Catch up memory after the 4-day model outage: run the distill + embed chain once.
- [~] N4. D2 serving path: `chad-tunnel-container.sh` + plist (OpenShell `forward service` for
      8901 and 18789→18791) written, not installed (`05bcf99f61`). Test at the cutover: starting
      chad-next runs its missed crons at boot, so it isn't started beforehand.
- [ ] N5. D1 leftovers: the 4 presets rejected on chad-next (agent-browser, github-tools,
      openclaw-bundled, session-logs) — only appliable after the pod cluster stops (U5).
- [ ] C12. Signup → confirmation email + invite (auto-mint or one-click approve). After U7/U8.

## 3. Watch

- New doctor check sends a real completion (the 10-20 model withdrawal went unnoticed 4 days).
- Weekly upstream sync, Mondays 09:00, now in `~/.nemoclaw/worktrees/upstream-sync`.
- NemoClaw `main` "Check cloudflared Release Pin" fails until upstream bumps 2026.10.1
  (upstream's automation; comes with the sync).

## 4. Done

### 2026-10-24

- [x] N1. upstream/main (49 commits) merged into local chad-dev (`068948d748`): clean; tests
      29,454 (+328), 17 failing before and after, 0 new (all 17 in upstream code and
      environment-bound: ghcr base-image lookups, live gateway state in /tmp).
- [x] N3. Docs: `docs/design/SMITHERS-GATES-AUDIT-2026-10.md`; chad-ops runbook lists the
      inference and Proton-build doctor checks.

- [x] NVIDIA withdrew `nemotron-3-super-120b-a12b` (~10-20): every reply + all 10 crons failed
      for 4 days, no backup since 10-20. Route → `nemotron-3.5-super-vl-120b-a12b`; Chad's
      defaults updated; doctor tests a real reply (`b46a07241b`). Backup current; crons verified.
- [x] Token-optimize audit: 259 runs, 344 proposals, 0 applied; could not save anything (the
      sandbox route forces one model; NVIDIA hosted isn't billed per token). Off the schedule.
      Shadow workflows no longer raise approval gates; 17 pending gates cleared (`196cd82b3b`).
- [x] Upstream sync job: runs in a dedicated worktree; clean merges no longer reported as
      conflicts (`73ed9e41ef`; live after U1).
- [x] Premium sync removes lapsed subscribers it added, never hand-added operators (`a528670d99`).
- [x] 16 e2e test emails had premium on live (Square sandbox webhooks hit the old Worker code) —
      removed from KV + allowlist.

### 2026-10-19 → 10-20

- Memory: gbrain vector(2048) + self-heal in recover; distiller (identity-anchored, user turns
  only, open-requests = pending work); memory-lancedb on; chat-prune; FF re-import.
- Reliability: autorecover (10 min) incl. shadow-:8080 + handshake heal; cron-reload order +
  `--no-deliver`; backup manifest/retry/skip-unchanged; inference timeout 300 s; Docker images
  84.5 → 22.1 GB; rehearsal sandbox runs no crons; memory-lancedb external-plugin install.
- Paywall: premium-sync every 15 min; webhook hardening (once per payment, own checkout only,
  amount check, refunds) live with D1 `square_payments`; site serves only index.html + assets
  (`.assetsignore`); dead Pages workflow removed; landing branches cleaned (archive/* tags).
- Skills/docs: proton skill fixed; budget.json backed up; runbooks (embed model, budget path).
- Smithers 0.32; gstack 1.91.158; "Agent Runs" link in OWUI; chad-next startup cause found.
