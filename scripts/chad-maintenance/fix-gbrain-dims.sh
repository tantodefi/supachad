#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Re-apply the 2026-10-01 gbrain migration on chad: content_chunks.embedding
# vector(1536)+hnsw -> vector(2048), no hnsw. nemotron-3-embed-1b emits 2048d and
# pgvector HNSW caps at 2000 dims. The brain was re-initialized (fresh 1536
# schema), so every embed now fails "expected 1536 dimensions, not 2048".
# Refuses if any chunk is already embedded. Then starts the embed backfill.
set -euo pipefail
ssh openshell-chad 'cat > /tmp/gbrain-dim2048.mjs' <<'EOF'
import { PGlite } from "/usr/local/lib/gbrain/node_modules/@electric-sql/pglite/dist/index.js";
import { vector } from "/usr/local/lib/gbrain/node_modules/@electric-sql/pglite/dist/vector/index.js";
import { pg_trgm } from "/usr/local/lib/gbrain/node_modules/@electric-sql/pglite/dist/contrib/pg_trgm.js";
const db = await PGlite.create({ dataDir: "/sandbox/.gbrain/brain.pglite", extensions: { vector, pg_trgm } });
const typ = async () => (await db.query(`SELECT format_type(a.atttypid, a.atttypmod) t FROM pg_attribute a WHERE a.attrelid='content_chunks'::regclass AND a.attname='embedding'`)).rows[0]?.t;
console.log("before:", await typ());
const n = (await db.query(`SELECT count(*)::int c FROM content_chunks WHERE embedding IS NOT NULL`)).rows[0].c;
if (n > 0) { console.error(`refusing: ${n} chunks already embedded`); process.exit(1); }
for (const r of (await db.query(`SELECT indexname FROM pg_indexes WHERE tablename='content_chunks' AND indexdef ILIKE '%hnsw%'`)).rows) {
  console.log("drop index", r.indexname); await db.exec(`DROP INDEX IF EXISTS "${r.indexname}"`);
}
await db.exec(`ALTER TABLE content_chunks ALTER COLUMN embedding TYPE vector(2048)`);
console.log("after:", await typ());
await db.close();
EOF
ssh -n openshell-chad 'bun /tmp/gbrain-dim2048.mjs'
ssh -n openshell-chad 'nohup sh -c "HOME=/sandbox gbrain embed --stale > /tmp/gbrain-embed.log 2>&1; echo EMBED_EXIT=\$? >> /tmp/gbrain-embed.log" >/dev/null 2>&1 & echo "embed backfill started (log: /tmp/gbrain-embed.log)"'
