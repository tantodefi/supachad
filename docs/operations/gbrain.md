---
title:
  page: "GBrain in NemoClaw"
  nav: "GBrain"
description:
  main: "How NemoClaw embeds gbrain inside the chad sandbox: the vendored embedder patches, NVIDIA NIM wiring, daily memory workflow, and recovery playbook."
  agent: "Operator guide for the gbrain knowledge store inside the chad sandbox. Use when the user asks why embeddings fail, how to rebuild the brain, where memory pages live, or how to add a new cron consumer."
keywords: ["gbrain", "pglite", "nvidia nim", "embeddings", "chad memory", "nemoclaw brain"]
topics: ["generative_ai", "ai_agents", "memory"]
tags: ["openclaw", "openshell", "gbrain", "chad", "operations", "memory"]
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

# GBrain in NemoClaw

[`gbrain`](https://github.com/tantodefi/gbrain) is the knowledge store the chad
sandbox uses for hybrid (vector + graph) recall. It runs entirely inside the
sandbox: a [PGLite](https://pglite.dev/) WASM Postgres holds pages, chunks, and
embeddings, and an external embedder is called over HTTPS to vectorize content
on `gbrain put`.

NemoClaw ships a small vendored overlay (see
`scripts/gbrain-patches/`) so the same gbrain binary can target NVIDIA NIM
embeddings instead of the upstream OpenAI default. With no env vars set the
overlay is a no-op — upstream behavior is preserved.

## Architecture

```text
┌──────────────────────────── chad sandbox ────────────────────────────┐
│                                                                       │
│   /usr/local/bin/gbrain        (wrapper — exports env from config)    │
│        │                                                              │
│        ▼                                                              │
│   /usr/local/bin/gbrain-bin    (real binary, bun-built)               │
│        │                                                              │
│        ├─→ /sandbox/.gbrain/brain.pglite/   (PGLite store, on disk)   │
│        │                                                              │
│        └─→ HTTPS embeddings ─────────────────────────────┐            │
│                                                          │            │
└──────────────────────────────────────────────────────────┼────────────┘
                                                           ▼
                                              integrate.api.nvidia.com
                                              /v1/embeddings
                                              (nvidia/llama-3.2-nv-embedqa-1b-v2)
```

Three pieces are NemoClaw-specific:

1. **`scripts/gbrain-patches/`** — overlay applied in the Dockerfile after `bun add`. Makes the embedder model + dimensions + input_type configurable via env vars (upstream hardcodes `text-embedding-3-large` @ 1536 dims). See `scripts/gbrain-patches/README.md` for the full diff and sync-with-upstream procedure.
2. **`scripts/sandbox-bin/gbrain-wrapper.sh`** — wrapper installed at `/usr/local/bin/gbrain`. Reads `/sandbox/.gbrain/config.json`, exports the relevant env vars (`OPENAI_BASE_URL`, `OPENAI_API_KEY`, `GBRAIN_EMBED_*`), then `exec`s the real `gbrain-bin`. The OpenAI SDK inside gbrain auto-picks-up `OPENAI_BASE_URL` / `OPENAI_API_KEY`.
3. **`scripts/chad-cron-wrappers/chad-gbrain-dream`** + `chad-log-event` — daily maintenance + structured event log that the cron prompts call. See the Memory workflow section below.

## Embedder env-var contract

The patched gbrain reads these at module load. Defaults reproduce upstream behavior.

| Env var | Config field (`config.json`) | Default | Purpose |
|---|---|---|---|
| `OPENAI_BASE_URL` | `openai_base_url` | `https://api.openai.com/v1` | Endpoint. Set to `https://integrate.api.nvidia.com/v1` for NIM. |
| `OPENAI_API_KEY` | `openai_api_key` | wrapper falls back to `unused` | OpenAI SDK refuses to construct without it, even when the endpoint doesn't auth. |
| `GBRAIN_EMBED_MODEL` | `embed_model` | `text-embedding-3-large` | NIM model id. We use `nvidia/nemotron-3-embed-1b`. |
| `GBRAIN_EMBED_DIMENSIONS` | `embed_dimensions` | `1536` | Must match what the model returns. We use `2048`. **Schema column is sized at `gbrain init` time** (always `vector(1536)`); `chad-ops gbrain-dims` migrates it to 2048 (see below). |
| `GBRAIN_EMBED_INPUT_TYPE` | `embed_input_type` | (unset) | NIM-only. `passage` for indexing, `query` for retrieval. We set `passage`; gbrain's embed signature doesn't yet distinguish. |
| `GBRAIN_EMBED_PASS_DIMENSIONS` | (none) | `true` | Set `false` for fixed-dim NIM models that reject the `dimensions` param. |

The NemoClaw setup configures NVIDIA NIM with `nvidia/nemotron-3-embed-1b` at 2048 dimensions — see `scripts/chad-setup.sh` step 3b. pgvector's HNSW index caps at 2000 dimensions, so the 2048 column has no HNSW index.

## Sandbox layout

| Path | What it is |
|---|---|
| `/sandbox/.gbrain/config.json` | Embedder config (mode 0600). Wrapper reads this. |
| `/sandbox/.gbrain/brain.pglite/` | PGLite data dir (pages, chunks, embeddings, links, timeline). |
| `/sandbox/.openclaw/workspace/memory/` | Daily journals (`<date>.md`) + structured event logs (`events-<date>.jsonl`). Source of truth that `chad-gbrain-dream` ingests. |
| `/usr/local/bin/gbrain` | Wrapper script. |
| `/usr/local/bin/gbrain-bin` | Real binary (bun-built). |
| `/usr/local/lib/gbrain/node_modules/gbrain/src/core/embedding.ts` | Patched at image build via Dockerfile overlay. |
| `/usr/local/lib/gbrain/node_modules/gbrain/src/core/pglite-schema.ts` | Same — patched at image build. |

## Memory workflow

NemoClaw uses three layers, following the gbrain "brain vs memory vs session"
guidance (`/Users/r/gbrain/docs/guides/brain-vs-memory.md`):

| Layer | Where | Lifetime | What it holds |
|---|---|---|---|
| **World** | gbrain pages | persistent | Textbooks, references, slugged docs (`memory/<date>`, `events/<date>`, `system/*`). Searched first. |
| **Operations** | `<workspace>/memory/<date>.md` + `events-<date>.jsonl` | rolling | Today's human journal and structured event log. Cron wrappers append here. |
| **Session** | conversation context | ephemeral | The current chat. |

### Daily files

- `<date>.md` — human-readable journal. Sections: `# <date>`, `## Events`, plus narrative additions.
- `events-<date>.jsonl` — one JSON object per line: `{ts, kind, source, summary, ref?}`.

Both are written by `chad-log-event`:

```bash
chad-log-event mail-in chad-mail-check \
  "tjcooke@protonmail.com: Squat depth question" \
  "msg-id:abc123"
```

Args: `<kind> <source> <summary> [ref]`. Quote the summary. Common kinds: `mail-in`, `mail-sent`, `draft`, `action`, `error`, `cron`.

### Daily ingest cycle (`chad-gbrain-dream`)

Runs from cron (see `scripts/chad-cron-wrappers/chad-gbrain-dream`). In a single
~60s window it:

1. Ensures today's memory file exists.
2. In parallel: `gbrain put system/<doc>` for each workspace doc (SOUL, USER, AGENTS, MEMORY, HEARTBEAT, TOOLS, EMAIL-POLICY, IDENTITY).
3. In parallel: `gbrain put memory/<date>` (the human journal) and `gbrain put events/<date>` (the JSONL stream).
4. `gbrain extract timeline --source db` — pulls dated events into the timeline graph.
5. `gbrain doctor` — health check.
6. Detaches the slow steps (`gbrain embed --stale`, `gbrain extract links`) via `nohup`. They finish in the background and the next dream cycle picks up wherever they left off.
7. Appends a `## Brain maintenance` block to today's `.md` summarizing what ran.

The detach pattern is required because `extract links` is LLM-driven and can
take minutes. The cron prompt only needs the synchronous portion to return
"completed" before the embedded model's exec tool times out.

## Common operations

### Inspect

```bash
gbrain stats                   # page / chunk / embedding counts
gbrain doctor                  # config + connectivity + schema check
gbrain query "<question>"      # hybrid search
gbrain get memory/2026-04-29   # read one page
gbrain list pages              # paginated page listing
```

### Add / update content

```bash
gbrain put system/notes --content "$(cat my-notes.md)"
gbrain put memory/2026-04-29 --content "$(cat /sandbox/.openclaw/workspace/memory/2026-04-29.md)"
```

`gbrain put` upserts by slug. The wrapper handles env-var setup automatically.

### Verify embedder

```bash
gbrain health
# Embed coverage: 100.0%   Missing embeddings: 0
gbrain stats
# Pages: 4076  Chunks: 4167  Embedded: 4167
```

The current embedder is `nvidia/nemotron-3-embed-1b` at **2048** dimensions
(`llama-nemotron-embed-1b-v2` @ 1536 hit EOL on 2026-08-25 with HTTP 410).
pgvector's HNSW index caps at 2000 dimensions, so the brain runs on a
`vector(2048)` column with **no HNSW index** (sequential scan; fine at this size).

If embeds fail, the usual causes are: a bad `OPENAI_API_KEY`, a retired model
id (HTTP 410), a dimension mismatch between the model and the schema column
(`expected 1536 dimensions, not 2048`), or the L7 binary allowlist missing
`bun` (see below). `chad-ops doctor` reports page count and embed coverage —
a health score alone passes an empty brain.

### L7 policy / binary identity

The OpenShell L7 proxy pins egress allowlist by `/proc/self/exe`. The
`gbrain` command on `$PATH` is a shell wrapper that execs
`/usr/local/bin/gbrain-bin`, which itself execs
`bun /…/cli.ts`. The TLS dial to NVIDIA NIM happens from the **bun**
process — not from `gbrain` or `gbrain-bin`. The `gbrain.yaml` policy
preset's `binaries:` list must include all three paths:

```yaml
binaries:
  - { path: /usr/local/bin/gbrain }
  - { path: /usr/local/bin/gbrain-bin }
  - { path: /usr/local/bin/bun }
```

Symptom when `bun` is missing from the allowlist: `curl: (56) CONNECT
tunnel failed, response 403` on every embed call. Confirm by tailing
`/sandbox/.openclaw-data/logs/config-audit.jsonl` (or running
`gbrain embed --stale` and watching the error). Same lesson as the
`feedback_openshell_l7_binary_identity` rule — wrapper paths must always
include the wrapped binary.

## Recovery: empty or wrong-dim brain

### Detect

An empty PGLite brain is already about 39 MB on disk, so never judge a brain by
size. Check pages and coverage:

```bash
ssh openshell-chad 'gbrain stats | head -3; gbrain health | grep -i coverage'
```

`Pages: 0` means the brain was re-initialized (the 2026-10-14 reboot found it
empty). `chad-ops doctor` flags this as `gbrain EMPTY`.

### Restore pages from chad-state

The 6-hourly `workspace-backup` cron exports every page as Markdown to
`brain/` in the `chad-state` repo (additive — an empty brain never deletes the
export). Pull that folder on the host and import it without embedding:

```bash
# Host: download only brain/ from chad-state (owner token from the git keychain)
curl -fsSL -H "Authorization: token $TOKEN" \
  https://api.github.com/repos/tantodefi/chad-state/tarball/main \
  | tar xz --strip-components=1 --include='*/brain/*'
# Stream it in as base64 — raw binary over the sandbox ssh proxy corrupts.
COPYFILE_DISABLE=1 tar czf - --no-xattrs brain | base64 \
  | ssh openshell-chad 'mkdir -p /tmp/brain-restore && base64 -d | tar xzf - -C /tmp/brain-restore'
ssh openshell-chad 'HOME=/sandbox gbrain import /tmp/brain-restore/brain --no-embed'
```

### Fix the schema dimension

A fresh `gbrain init` creates `vector(1536)` plus an HNSW index. Before
embedding, migrate the column to 2048 and drop the index. The migration
refuses to run if any chunk is already embedded:

```bash
bash scripts/chad-ops.sh gbrain-dims   # vector(1536)+hnsw → vector(2048), no hnsw; idempotent
```

It imports PGLite by absolute path
(`/usr/local/lib/gbrain/node_modules/@electric-sql/pglite/dist/…`) because Bun
resolves bare imports relative to the script file, and the gbrain package
directory is root-owned.

### Re-write the config after any init

`gbrain init` overwrites `config.json` with only `engine` and `database_path`,
so the next call goes out as `OPENAI_API_KEY=unused` and gets a 401. Run
`chad-ops gbrain-config` after every init.

### Embed and verify

```bash
ssh openshell-chad 'nohup gbrain embed --stale > /tmp/gbrain-embed.log 2>&1 &'
# 4167 chunks take a few minutes against NVIDIA NIM.
ssh openshell-chad 'gbrain health | head -3'
# Embed coverage: 100.0%
ssh openshell-chad 'gbrain query "squat programming for a novice lifter"'
# expect starting-strength-chunk-* hits around 0.8+
```

The canonical entry point for re-ingesting fitness reference content is
`/usr/local/bin/chad-ingest-fitness-books` (deployed by `chad-setup.sh`).
That script downloads OCR text from archive.org and chunks it into
~1500-char pages tagged `fitness, starting-strength, book-chunk` or
`fitness, supple-leopard, book-chunk`.

## Why we patched

`gbrain` v0.14.x hardcodes `text-embedding-3-large` at 1536 dimensions in two
files (`src/core/embedding.ts`, `src/core/pglite-schema.ts`); the
`embed_model` field in `config.json` is read on save but never on use. NVIDIA
NIM doesn't serve `text-embedding-3-large`, and its embed-qa models return
1024 / 2048 dims. Without the patch, gbrain cannot talk to NIM at all.

The overlay is two files copied over the upstream sources after `bun add`
runs. With no env vars set, behavior is identical to upstream. When upstream
gains native config-driven embedder support, **delete the overlay** —
`scripts/gbrain-patches/README.md` covers the sync-with-upstream procedure.

## Related docs

- `scripts/gbrain-patches/README.md` — patch contents, env-var reference, Dockerfile mechanism, upstream-sync procedure.
- `docs/operations/chad-workflows.md` — broader cron + workflow context.
- `docs/operations/log-locations.md` — where gbrain logs land.
