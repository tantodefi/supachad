#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Seed the vetted starter skills (*.md next to this file) into OpenWebUI, make them
# readable by everyone, attach them to chad-lite, and set the welcome-screen prompt
# suggestions. Idempotent: existing skills are updated in place.
#
# A skill here is instructions only (name/description/markdown) — no code runs on
# the server. Users can also invoke one in chat with `$`.
#
# Env: OWUI_URL (default http://127.0.0.1:3000),
#      CHAD_HOST_CREDS (default ~/.nemoclaw/credentials.json; OPENWEBUI_API_KEY_TANTODEFI)

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
OWUI = os.environ.get("OWUI_URL", "http://127.0.0.1:3000") + "/api/v1"
KEY = json.loads(Path(os.environ.get("CHAD_HOST_CREDS", Path.home() / ".nemoclaw/credentials.json")).read_text())[
    "OPENWEBUI_API_KEY_TANTODEFI"
]
LITE = "chad-lite"
# Public = user:* (the separate "anyone" type is stripped by the skills route).
PUBLIC = [{"principal_type": "user", "principal_id": "*", "permission": "read"}]

SUGGESTIONS = [
    {"title": ["Summarize this", "paste text or a link"], "content": "Summarize this for me: "},
    {"title": ["Plan my week", "goals → checklist"], "content": "Help me plan my week. My goals are: "},
    {"title": ["Research a question", "with sources"], "content": "Research this and cite your sources: "},
    {"title": ["Polish my writing", "clearer and tighter"], "content": "Edit this to be clearer and more concise: "},
    {"title": ["Explain a concept", "step by step"], "content": "Explain this to me like a tutor, step by step: "},
    {"title": ["Start strength training", "a safe beginner plan"], "content": "Build me a beginner strength training plan. About me: "},
]


def call(method, path, body=None):
    req = urllib.request.Request(
        OWUI + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + KEY, "content-type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def parse(path: Path) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", path.read_text(), re.S)
    if not m:
        raise SystemExit(f"{path.name}: missing frontmatter")
    front = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
    front = {k.strip(): v.strip() for k, v in front.items()}
    tags = [t.strip() for t in front.get("tags", "").strip("[]").split(",") if t.strip()]
    return {"id": front["id"], "name": front["name"], "description": front["description"],
            "content": m.group(2).strip() + "\n", "meta": {"tags": tags}, "is_active": True}


def main() -> int:
    ids = []
    for path in sorted(HERE.glob("*.md")):
        skill = parse(path)
        try:
            call("GET", f"/skills/id/{skill['id']}")
            call("POST", f"/skills/id/{skill['id']}/update", skill)
            verb = "updated"
        except urllib.error.HTTPError as e:
            if e.code not in (401, 404):
                raise
            call("POST", "/skills/create", {**skill, "access_grants": PUBLIC})
            verb = "created"
        call("POST", f"/skills/id/{skill['id']}/access/update", {"access_grants": PUBLIC})
        ids.append(skill["id"])
        print(f"{verb} skill {skill['id']} ({skill['name']})")

    model = call("GET", f"/models/model?id={LITE}")
    meta = model.get("meta") or {}
    if sorted(meta.get("skillIds") or []) != sorted(ids):
        meta["skillIds"] = ids
        call("POST", f"/models/model/update?id={LITE}", {
            "id": LITE, "name": model["name"], "base_model_id": model.get("base_model_id"),
            "meta": meta, "params": model.get("params") or {}, "is_active": model.get("is_active", True),
        })
        print(f"attached {len(ids)} skills to {LITE}")

    call("POST", "/configs/suggestions", {"suggestions": SUGGESTIONS})
    print(f"set {len(SUGGESTIONS)} welcome-screen prompt suggestions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
