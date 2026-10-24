<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Chad maintenance scripts

One-off and runbook repair scripts for the live chad sandbox, run from the Mac
host (any working directory; each one reaches chad via `ssh openshell-chad` or
`docker exec`). Recurring operations belong in `scripts/chad-ops.sh` instead.

| Script | When to run |
|---|---|
| `fix-ff-mislabel.sh` | Once: deletes the 313 pages titled "Starting Strength — chunk NNNN/0313" that hold Ferocious Fitness text. |
| `fix-gbrain-dims.sh` | After a brain re-init leaves `content_chunks.embedding` at vector(1536); moves it to vector(2048) and backfills embeddings. |
| `fix-handshake.sh` | Cold start: after a cluster restart rotates `OPENSHELL_SSH_HANDSHAKE_SECRET` and `ssh openshell-chad` fails. |
| `restore-cli-mtls.sh` | When the host openshell CLI mTLS bundle no longer matches the live 0.0.20 gateway. |
