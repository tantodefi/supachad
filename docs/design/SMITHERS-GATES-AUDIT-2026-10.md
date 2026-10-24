<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Smithers approval gates and token-optimize: audit (2026-10-24)

Question: the Moshi approval notifications from runs.supachad.com are mostly token-optimize
runs. Does that system, or any gated workflow, produce real results?

Answer: no. None of the gated workflows could change anything when approved, and
token-optimize could not save money even if it applied its proposals.

## Evidence

Counts from the workflow SQLite databases in `scripts/chad-smithers/` on 2026-10-24.

| Workflow | Runs (all / last 7 d) | Gates never answered | What approving does |
|---|---|---|---|
| token-optimize | 259 / 21 | 164 | Logs "SHADOW: would set …" (`CHAD_TOKENOPT_APPLY` unset) |
| bug-report | — / 7 | 57 | Logs "would file N issue(s)" (`CHAD_BUGREPORT_POST` unset) |
| self-improve | — / 7 | 57 | Logs "would apply" (`CHAD_SELFIMPROVE_APPLY` unset) |
| skill-improve | — / 7 | 56 | Logs "would file" (`CHAD_SKILLIMPROVE_POST` unset) |

token-optimize proposed 344 model downgrades across 74 finished runs and applied none.
The proposals did not converge: consecutive runs proposed 0, 2, 0, 2 or 4 downgrades
for the same eight candidates.

## Why token-optimize cannot save anything

1. Chad's NVIDIA calls go through the sandbox route `inference.local`. The route rewrites
   every request to the single configured model, so a per-task model in
   `scripts/task-profiles.json` never reaches NVIDIA. Every probe on 2026-10-24 hit the same
   NVIDIA function regardless of the model named.
2. NVIDIA-hosted Nemotron through build.nvidia.com is not billed per token, so a cheaper
   model saves no money. The only per-token spend is the Claude premium path
   (`premium-*` profiles), which token-optimize never benchmarks.
3. Its candidates went stale: `nemotron-3-super-120b-a12b` was withdrawn around 2026-10-20,
   so runs after that compared against a model that returns 404.

## Changes

- token-optimize no longer runs on the schedule (`run-experiments.sh`);
  `CHAD_RUN_TOKENOPT=1` runs it.
- The four workflows request approval only when their apply or post flag is on. Shadow
  results stay in the runs UI, and Moshi receives no gate for a run that cannot act.
- The 17 pending gates were denied. Each run completed its shadow path.

## What still produces something

- `experiments.jsx`: no gates. Its champion drafter prompt (`state/champion-prompt.json`) is
  used by the draft role in host Smithers workflows. Those workflows ran zero times in the
  last week, so the practical effect is small.
- `mcp-health-probe` and `fail-only-report`: hourly health checks with real signal.

## Open decision

Enable or retire bug-report, self-improve and skill-improve. Enabled, they file GitHub issues
or tune cron settings after an approval, and an approval then has an effect. Retired, they
stop running. Keeping them in shadow mode costs inference without producing anything.
