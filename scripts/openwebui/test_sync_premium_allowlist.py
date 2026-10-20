# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""sync-premium-allowlist.sh against a fake Worker: adds subscribers, removes the
ones it added when they lapse, never touches hand-added operators."""
import http.server
import json
import os
import subprocess
import threading
from pathlib import Path

SCRIPT = Path(__file__).with_name("sync-premium-allowlist.sh")
OPS = ["tantodefi@proton.me", "tjcooke@protonmail.com"]


class Worker:
    emails: list = []

    def __init__(self):
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"ok": True, "emails": outer.emails}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/api/premium/list"


def run(tmp, worker, emails, **env):
    worker.emails = emails
    e = {**os.environ, "CHAD_HOST_CREDS": str(tmp / "creds.json"), "CHAD_PREMIUM_MANAGED": str(tmp / "managed.json"),
         "PREMIUM_LIST_URL": worker.url, "PREMIUM_ADMIN_SECRET": "x", "CHAD_OPS_BIN": "true", **env}
    r = subprocess.run(["bash", str(SCRIPT)], env=e, capture_output=True, text=True)
    allow = json.load(open(tmp / "creds.json"))["CHAD_OPERATOR_ALLOWLIST"]
    return r, sorted(allow)


def setup(tmp):
    (tmp / "creds.json").write_text(json.dumps({"CHAD_OPERATOR_ALLOWLIST": OPS}))
    return Worker()


def test_adds_then_removes_lapsed_subscriber(tmp_path):
    w = setup(tmp_path)
    _, allow = run(tmp_path, w, ["a@x.co"])
    assert allow == sorted(OPS + ["a@x.co"])
    r, allow = run(tmp_path, w, [])
    assert allow == sorted(OPS), r.stderr
    assert "removed=1" in r.stdout


def test_hand_added_operators_never_removed(tmp_path):
    w = setup(tmp_path)
    run(tmp_path, w, [OPS[0], "a@x.co"])  # operator also has a subscription
    _, allow = run(tmp_path, w, [])
    assert allow == sorted(OPS)


def test_mass_removal_refused(tmp_path):
    w = setup(tmp_path)
    subs = [f"s{i}@x.co" for i in range(4)]
    run(tmp_path, w, subs)
    r, allow = run(tmp_path, w, [])
    assert r.returncode != 0 and "refusing" in r.stderr
    assert allow == sorted(OPS + subs)
    r, allow = run(tmp_path, w, [], CHAD_PREMIUM_ALLOW_MASS_REMOVE="1")
    assert r.returncode == 0 and allow == sorted(OPS)


def test_no_change_is_noop(tmp_path):
    w = setup(tmp_path)
    run(tmp_path, w, ["a@x.co"])
    r, _ = run(tmp_path, w, ["a@x.co"])
    assert "NOCHANGE" in r.stdout
