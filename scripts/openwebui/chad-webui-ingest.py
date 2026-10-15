#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# chad-webui-ingest — daily push of OpenWebUI chat history into sandbox gbrain.
#
# OpenWebUI's webui.db lives on the host (~/.nemoclaw/openwebui/data/webui.db),
# disconnected from the sandbox's gbrain. Without this script, chats users
# have with the agent via OpenWebUI never reach the long-term memory consolidation
# pipeline — chad-gbrain-dream sees the agent's session log but not the
# human-facing transcript / title / structure.
#
# This script reads chats updated since LOOKBACK_HOURS, formats each as
# markdown, and ships it to the sandbox via SSH for `gbrain put chat/<id>`.
# Idempotent — `gbrain put` upserts by slug.
#
# Schedule: ~/Library/LaunchAgents/dev.nemoclaw.chad-webui-ingest.plist,
# 04:30 UTC (1h after chad-gbrain-dream so the dream digest reflects today).

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(os.environ["HOME"])
DB = Path(os.environ.get("WEBUI_DB", HOME / ".nemoclaw/openwebui/data/webui.db"))
SSH_HOST = os.environ.get("CHAD_SSH_HOST", "openshell-chad")
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "25"))
LOG_FILE = Path(os.environ.get("WEBUI_INGEST_LOG", HOME / ".nemoclaw/openwebui/webui-ingest.log"))
PUT_TIMEOUT = int(os.environ.get("PUT_TIMEOUT", "60"))
# Per-chat high-water mark of the updated_at we last pushed successfully. A
# fixed lookback window silently dropped every chat whose push failed: the
# 2026-10-08..10 sweeps all hit transport errors and those chats aged out.
STATE_FILE = Path(os.environ.get("WEBUI_INGEST_STATE", HOME / ".nemoclaw/openwebui/webui-ingest-state.json"))


def load_state() -> dict[str, float]:
    try:
        return {str(k): float(v) for k, v in json.loads(STATE_FILE.read_text()).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def save_state(state: dict[str, float]) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=0, sort_keys=True))
    tmp.replace(STATE_FILE)


def log(msg: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    sys.stderr.write(line)
    with LOG_FILE.open("a") as f:
        f.write(line)


def normalize_messages(chat_obj: dict) -> list[dict]:
    """Return the active conversation thread in chronological order.

    OpenWebUI 0.11 keeps the authoritative conversation in ``history``: a dict
    of messages keyed by id, linked by ``parentId``, with ``currentId`` naming
    the latest message on the active branch. The top-level ``messages`` list
    can be stale — it often holds only the first user turn — so reading it
    first dropped every assistant reply (2026-10-14: pages of ~300 bytes from
    ~8 KB chats). Walk currentId → root instead, and fall back to the list.
    """
    history = chat_obj.get("history")
    if isinstance(history, dict) and isinstance(history.get("messages"), dict):
        by_id = history["messages"]
        cur = history.get("currentId")
        if cur not in by_id and by_id:
            # No currentId: take the newest leaf on any branch.
            leaves = [m for m in by_id.values() if not m.get("childrenIds")]
            cur = max(leaves or by_id.values(), key=lambda m: m.get("timestamp") or 0).get("id")
        thread, seen = [], set()
        while cur in by_id and cur not in seen:
            seen.add(cur)
            thread.append(by_id[cur])
            cur = by_id[cur].get("parentId")
        if thread:
            return list(reversed(thread))
    msgs = chat_obj.get("messages")
    if isinstance(msgs, list):
        return msgs
    if isinstance(msgs, dict):
        items = list(msgs.values())
        items.sort(key=lambda m: m.get("timestamp") or m.get("created_at") or 0)
        return items
    return []


def iso(ts) -> str:
    """Epoch seconds (or ms) → ISO-8601 UTC; pass anything else through."""
    try:
        v = float(ts)
    except (TypeError, ValueError):
        return str(ts)
    if v > 1e12:
        v /= 1000
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(v))


def operator_slug(email: str) -> str:
    """tantodefi@proton.me → tantodefi (gbrain people/<slug> entity page)."""
    local = (email or "").split("@", 1)[0].lower()
    return re.sub(r"[^a-z0-9-]+", "-", local).strip("-") or "unknown"


def render_person(slug: str, email: str, chats: list[tuple]) -> str:
    """people/<slug>: who the operator is to Chad + a dated chat timeline.

    Deterministic (no LLM): gbrain's timeline extractor reads
    `- **YYYY-MM-DD** | summary` lines, and every chat page links back here
    via `[slug](people/slug)`, so `gbrain extract links|timeline` finally
    builds a graph instead of reporting 0% coverage.
    """
    lines = [
        "---",
        "type: person",
        f"title: {json.dumps(slug)}",
        f"email: {email}",
        "source: openwebui",
        f"chats: {len(chats)}",
        "---",
        "",
        f"# {slug}",
        "",
        f"OpenWebUI operator `{email}`. {len(chats)} chats with Chad.",
        "",
        "## Timeline",
        "",
    ]
    for chat_id, title, updated_at in chats:
        lines.append(f"- **{iso(updated_at)[:10]}** | {title or '(untitled)'} (chat/{chat_id})")
    return "\n".join(lines) + "\n"


def extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def render_chat(
    chat_id: str, user_id: str, title: str, created_at, updated_at, chat_obj: dict, operator: str = ""
) -> str:
    lines = [
        "---",
        f"chat_id: {chat_id}",
        f"user_id: {user_id}",
        f"operator: {operator or 'unknown'}",
        f"title: {json.dumps(title or '(untitled)')}",
        f"created_at: {iso(created_at)}",
        f"updated_at: {iso(updated_at)}",
        "source: openwebui",
        "---",
        "",
        f"# {title or '(untitled)'}",
        "",
        f"Operator: [{operator_slug(operator)}](people/{operator_slug(operator)})" if operator else "Operator: unknown",
        "",
    ]
    for i, m in enumerate(normalize_messages(chat_obj), start=1):
        if not isinstance(m, dict):
            continue
        role = (m.get("role") or "?").title()
        ts = m.get("timestamp") or m.get("created_at")
        head = f"## {i}. {role}"
        if ts:
            head += f" — {iso(ts)}"
        lines.append(head)
        lines.append("")
        body = extract_text(m.get("content")).strip()
        # Nest the message's own headings under its "## N. Role" turn heading
        # (outside code fences) so a reply's "## Summary" isn't read as a turn.
        nested, fence = [], False
        for bl in body.split("\n"):
            if bl.lstrip().startswith(("```", "~~~")):
                fence = not fence
            if not fence and re.match(r"#{1,4} ", bl):
                bl = "##" + bl
            nested.append(bl)
        lines.append("\n".join(nested) or "_(empty)_")
        lines.append("")
    return "\n".join(lines)


def push_to_sandbox(slug: str, markdown: str) -> tuple[bool, str]:
    # gbrain put takes --content <string>; passing megabytes through argv
    # risks E2BIG. Stream the content to a temp file inside the sandbox
    # via SSH stdin, then invoke gbrain put against the temp.
    remote_script = (
        'set -eu; tmp=$(mktemp); '
        'cat >"$tmp"; '
        f'gbrain put {slug!r} --content "$(cat \"$tmp\")"; '
        'rm -f "$tmp"'
    )
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", SSH_HOST, remote_script],
            input=markdown.encode("utf-8"),
            capture_output=True,
            timeout=PUT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except FileNotFoundError:
        return False, "ssh not found on host"
    if result.returncode != 0:
        return False, (result.stderr.decode("utf-8", "replace")[:240] or f"exit {result.returncode}")
    return True, "ok"


def prune_deleted(apply: bool) -> None:
    """Delete chat/<id> brain pages whose OpenWebUI chat no longer exists.

    Chats deleted in OpenWebUI kept their gbrain pages forever: by 2026-10-15
    1,935 of 2,005 chat pages were copies of automation-spawned chats that
    were long gone (1,787 of one "Proton Mail auth reset" prompt), drowning
    search. Archived chats are kept. Dry run unless apply=True.
    """
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    keep = {r[0] for r in con.execute("SELECT id FROM chat")}
    con.close()
    if not keep:
        log("prune: OpenWebUI has 0 chats — refusing to prune (wrong DB?)")
        return
    # gbrain list caps at 100 rows; export is the only full enumeration.
    listing = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", SSH_HOST,
         "set -eu; d=$(mktemp -d); gbrain export --dir \"$d\" >/dev/null 2>&1; "
         "ls \"$d/chat\" 2>/dev/null | sed 's/\\.md$//'; rm -rf \"$d\""],
        capture_output=True, timeout=600,
    )
    if listing.returncode != 0:
        log(f"prune: brain listing failed: {listing.stderr.decode('utf-8', 'replace')[:240]}")
        return
    in_brain = set(listing.stdout.decode().split())
    stale = sorted(in_brain - keep)
    log(f"prune: brain chat pages={len(in_brain)} openwebui chats={len(keep)} stale={len(stale)}"
        + ("" if apply else " (dry run; pass --apply to delete)"))
    if not apply or not stale:
        return
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", SSH_HOST,
         'n=0; while read -r s; do gbrain delete "chat/$s" >/dev/null 2>&1 && n=$((n+1)); done; echo "$n"'],
        input="\n".join(stale).encode() + b"\n", capture_output=True, timeout=3600,
    )
    log(f"prune: deleted {result.stdout.decode().strip() or 0} of {len(stale)} stale chat pages")


def main() -> None:
    if not DB.exists():
        log(f"webui.db missing at {DB}; nothing to ingest")
        return
    state = load_state()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    all_rows = con.execute(
        "SELECT c.id, c.user_id, c.title, c.created_at, c.updated_at, c.chat, "
        "COALESCE(u.email, '') FROM chat c LEFT JOIN user u ON u.id = c.user_id "
        "WHERE c.archived = 0 ORDER BY c.updated_at DESC",
    ).fetchall()
    con.close()
    # Pending = never pushed, or changed since the last successful push. With
    # no state yet, fall back to the lookback window so a first run on a big
    # instance doesn't replay years of chats (set LOOKBACK_HOURS=0 for all).
    if state:
        rows = [r for r in all_rows if float(r[4] or 0) > state.get(r[0], 0.0)]
    else:
        cutoff_ts = time.time() - LOOKBACK_HOURS * 3600 if LOOKBACK_HOURS > 0 else 0
        rows = [r for r in all_rows if float(r[4] or 0) > cutoff_ts]

    log(f"sweep start — db={DB} tracked={len(state)} pending={len(rows)}")
    pushed = skipped = failed = 0
    for chat_id, user_id, title, created_at, updated_at, chat_json, operator in rows:
        try:
            chat_obj = json.loads(chat_json or "{}")
        except json.JSONDecodeError as e:
            log(f"skip {chat_id}: bad JSON ({e})")
            skipped += 1
            continue
        if not normalize_messages(chat_obj):
            log(f"skip {chat_id}: no messages")
            skipped += 1
            state[chat_id] = float(updated_at or 0)  # nothing to ingest at this revision
            continue
        md = render_chat(chat_id, user_id, title, created_at, updated_at, chat_obj, operator)
        ok, detail = push_to_sandbox(f"chat/{chat_id}", md)
        if ok:
            pushed += 1
            state[chat_id] = float(updated_at or 0)
        else:
            failed += 1
            log(f"FAIL chat/{chat_id}: {detail}")
    # Rebuild each operator's people/<slug> page from ALL live chats (cheap: one
    # page per operator) so the timeline stays complete even when a sweep only
    # pushed a few changed chats.
    by_op: dict[str, list[tuple]] = {}
    for r in all_rows:
        if r[6]:
            by_op.setdefault(r[6], []).append((r[0], r[2], r[4]))
    for email, chats in sorted(by_op.items()):
        slug = operator_slug(email)
        ok, detail = push_to_sandbox(f"people/{slug}", render_person(slug, email, chats))
        if not ok:
            failed += 1
            log(f"FAIL people/{slug}: {detail}")
    save_state(state)
    log(f"sweep done — pushed={pushed} skipped={skipped} failed={failed} (failed chats retry next sweep)")


if __name__ == "__main__":
    if "--prune-deleted" in sys.argv:
        prune_deleted(apply="--apply" in sys.argv)
    else:
        main()
