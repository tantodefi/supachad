#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# chad-tiers-apply — enforce the OpenWebUI side of the two-tier model. Idempotent.
#
#   chad       the shared agent (gbrain, tools, operator context). Visible ONLY to
#              the operators in CHAD_OPERATOR_ALLOWLIST; the shim gate is the
#              server-side backstop. OpenWebUI memory is OFF for it, so no user's
#              OWUI memories are injected into (or reviewed by) the shared agent.
#   chad-lite  every other account's own assistant: public, the default + pinned
#              model, with OpenWebUI per-user memory (each account's memories are
#              its own). Free and paid signups both land here.
#   others     the curated open models: public.
#   terminals  the Docker Terminal + Browser VM (a root shell on the internal
#              network that can reach the chad shim) — operators only. The
#              injected terminal UI (vm-panel.js) shows only when the server
#              lists a terminal for the user, so this also hides the hover bar.
#
# Model visibility needs BYPASS_MODEL_ACCESS_CONTROL=False on the container (see
# docker-compose.yml). Grants are applied first, so flipping it never hides
# anything that should stay visible.
#
# Usage: chad-tiers-apply.py            apply everything
#        chad-tiers-apply.py --grants   only ensure model grants (nvidia-liveness
#                                       runs this so newly curated rows are public)
#        chad-tiers-apply.py --check    report drift, change nothing (exit 1 on drift)
#
# Env: WEBUI_DB (default ~/.nemoclaw/openwebui/data/webui.db),
#      CHAD_HOST_CREDS (default ~/.nemoclaw/credentials.json)

import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

WEBUI_DB = Path(os.environ.get("WEBUI_DB", Path.home() / ".nemoclaw/openwebui/data/webui.db"))
CREDS = Path(os.environ.get("CHAD_HOST_CREDS", Path.home() / ".nemoclaw/credentials.json"))

AGENT = "chad"
LITE = "chad-lite"
LITE_SYSTEM = (
    "You are Chad Lite, this user's own assistant on supachad. You are separate from "
    "the operators' Chad agent: you have no access to private files, calendars, email, "
    "or anyone else's data or chats. You do remember what this user chooses to share "
    "with you across chats (their personal memory, which only they can see and edit "
    "under Settings > Personalization). Use web search when a question needs current "
    "facts, and cite what you use. Be direct, honest and useful."
)
LITE_DESCRIPTION = (
    "Your own assistant — NVIDIA Nemotron 3 Nano with web search and a personal "
    "memory only you can see. Separate from the operators' Chad agent."
)


def operators() -> list[str]:
    raw = json.loads(CREDS.read_text()).get("CHAD_OPERATOR_ALLOWLIST", [])
    items = raw if isinstance(raw, list) else str(raw).split(",")
    return sorted({e.strip().lower() for e in items if e and "@" in e})


def now() -> int:
    return int(time.time())


class Tiers:
    def __init__(self, con: sqlite3.Connection, check: bool):
        self.con, self.check, self.drift = con, check, []

    def change(self, what: str, sql: str, args: tuple = ()) -> None:
        self.drift.append(what)
        if not self.check:
            self.con.execute(sql, args)

    def grants(self, op_ids: list[str]) -> None:
        rows = self.con.execute(
            "SELECT resource_id, principal_type, principal_id FROM access_grant "
            "WHERE resource_type = 'model' AND permission = 'read'"
        ).fetchall()
        have = {(r, t, p) for r, t, p in rows}
        for (mid,) in self.con.execute("SELECT id FROM model"):
            if mid == AGENT:
                continue
            if (mid, "user", "*") not in have:
                self.change(f"grant public read on {mid}", "INSERT INTO access_grant VALUES (?,?,?,?,?,?,?)",
                            (str(uuid.uuid4()), "model", mid, "user", "*", "read", now()))
        if (AGENT, "user", "*") in have:
            self.change(f"revoke public read on {AGENT}",
                        "DELETE FROM access_grant WHERE resource_type='model' AND resource_id=? AND principal_type='user' AND principal_id='*'",
                        (AGENT,))
        for uid in op_ids:
            if (AGENT, "user", uid) not in have:
                self.change(f"grant {AGENT} to operator {uid}", "INSERT INTO access_grant VALUES (?,?,?,?,?,?,?)",
                            (str(uuid.uuid4()), "model", AGENT, "user", uid, "read", now()))

    def model(self, mid: str, active: int | None = None, memory: bool | None = None,
              system: str | None = None, description: str | None = None) -> None:
        row = self.con.execute("SELECT is_active, meta, params FROM model WHERE id = ?", (mid,)).fetchone()
        if not row:
            self.drift.append(f"model {mid} missing (run chad-provision-tier0.sh)")
            return
        is_active, meta, params = row[0], json.loads(row[1] or "{}"), json.loads(row[2] or "{}")
        if active is not None and is_active != active:
            self.change(f"{mid} is_active -> {active}", "UPDATE model SET is_active=?, updated_at=? WHERE id=?",
                        (active, now(), mid))
        new_meta, new_params = json.loads(json.dumps(meta)), dict(params)
        if memory is not None:
            new_meta.setdefault("capabilities", {})["memory"] = memory
        if description is not None:
            new_meta["description"] = description
        if system is not None:
            new_params["system"] = system
        if new_meta != meta or new_params != params:
            self.change(f"{mid} meta/params", "UPDATE model SET meta=?, params=?, updated_at=? WHERE id=?",
                        (json.dumps(new_meta), json.dumps(new_params), now(), mid))

    def config(self, key: str, value) -> None:
        row = self.con.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        if row and json.loads(row[0]) == value:
            return
        if row:
            self.change(f"config {key} -> {value!r}", "UPDATE config SET value=?, updated_at=? WHERE key=?",
                        (json.dumps(value), now(), key))
        else:
            self.change(f"config {key} -> {value!r}", "INSERT INTO config (key, value, updated_at) VALUES (?,?,?)",
                        (key, json.dumps(value), now()))

    def terminals(self, op_ids: list[str]) -> None:
        row = self.con.execute("SELECT value FROM config WHERE key = 'terminal_server.connections'").fetchone()
        conns = json.loads(row[0]) if row else []
        want = [{"permission": "read", "principal_type": "user", "principal_id": u} for u in sorted(op_ids)]
        changed = False
        for c in conns:
            cur = sorted(((g.get("principal_type"), g.get("principal_id")) for g in (c.get("config") or {}).get("access_grants") or []))
            if cur != sorted(("user", u) for u in op_ids):
                c.setdefault("config", {})["access_grants"] = want
                changed = True
                self.drift.append(f"terminal {c.get('id')} -> operators only")
        if changed and not self.check:
            self.con.execute("UPDATE config SET value=?, updated_at=? WHERE key='terminal_server.connections'",
                             (json.dumps(conns), now()))

    def operator_pins(self, op_ids: list[str]) -> None:
        # Operators keep Chad as their pinned/default model; the site default is chad-lite.
        for uid in op_ids:
            (raw,) = self.con.execute("SELECT settings FROM user WHERE id = ?", (uid,)).fetchone()
            s = json.loads(raw or "{}") or {}
            ui = s.setdefault("ui", {})
            if ui.get("pinnedModels") is None:
                ui["pinnedModels"] = [AGENT, LITE]
                self.change(f"pin {AGENT} for operator {uid}", "UPDATE user SET settings=? WHERE id=?",
                            (json.dumps(s), uid))


def main() -> int:
    check = "--check" in sys.argv
    grants_only = "--grants" in sys.argv
    con = sqlite3.connect(str(WEBUI_DB), timeout=30)
    emails = operators()
    if not emails:
        print("error: CHAD_OPERATOR_ALLOWLIST is empty — refusing (would hide chad from everyone)", file=sys.stderr)
        return 2
    marks = ",".join("?" for _ in emails)
    op_ids = [r[0] for r in con.execute(f"SELECT id FROM user WHERE lower(email) IN ({marks})", emails)]
    t = Tiers(con, check)
    t.grants(op_ids)
    if not grants_only:
        t.model(AGENT, memory=False)
        t.model(LITE, active=1, memory=True, system=LITE_SYSTEM, description=LITE_DESCRIPTION)
        t.config("ui.default_models", LITE)
        t.config("ui.default_pinned_models", LITE)
        t.config("memories.background_review.enable", True)
        t.operator_pins(op_ids)
        t.terminals(op_ids)
    if not check:
        con.commit()
    con.close()
    for d in t.drift:
        print(("DRIFT " if check else "applied ") + d)
    print(f"[chad-tiers] operators={len(op_ids)}/{len(emails)} changes={len(t.drift)}{' (check only)' if check else ''}")
    return 1 if (check and t.drift) else 0


if __name__ == "__main__":
    sys.exit(main())
