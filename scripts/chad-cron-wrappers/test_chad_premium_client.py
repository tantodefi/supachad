# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for chad-premium-client — the Anthropic escalation wrapper.

Covers the two load-bearing, previously-untested paths plus two helpers:
  * _load_api_key  — env-first (ANTHROPIC_API_KEY) then credentials.json, the
    OpenShell-vault credential pattern; exits 2 when the key is absent.
  * _load_auth_context — the PremiumAccessDenied gate: premium is refused unless
    a readable auth-context file says allowsPremium=true.
  * _extract_text / _audit — Anthropic response parsing and best-effort auditing.

The network call (_call_anthropic, shelled to curl) is intentionally NOT invoked
here. Standalone-runnable with python3, or under pytest. The script is
extensionless so it's loaded via SourceFileLoader.
"""
import importlib.util
import json
import os
import tempfile
from importlib.machinery import SourceFileLoader
from pathlib import Path

_MOD_PATH = Path(__file__).with_name("chad-premium-client")


def _load_module():
    loader = SourceFileLoader("chad_premium_client_under_test", str(_MOD_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


_m = _load_module()


def _expect_exit(fn, code):
    try:
        fn()
    except SystemExit as e:
        assert e.code == code, f"expected exit {code}, got {e.code}"
        return
    assert False, f"expected SystemExit({code}), none raised"


def _set_env(**kw):
    for k, v in kw.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# --- _load_api_key: env-first vault pattern ------------------------------------

def test_api_key_env_first():
    _set_env(ANTHROPIC_API_KEY="  sk-env-key  ")
    try:
        _m.CREDS_FILE = "/nonexistent/creds.json"  # must NOT be consulted
        assert _m._load_api_key() == "sk-env-key"
    finally:
        _set_env(ANTHROPIC_API_KEY=None)


def test_api_key_creds_fallback():
    _set_env(ANTHROPIC_API_KEY=None)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "creds.json")
        json.dump({"ANTHROPIC_API_KEY": "sk-from-file"}, open(p, "w"))
        _m.CREDS_FILE = p
        assert _m._load_api_key() == "sk-from-file"


def test_api_key_missing_exits_2():
    _set_env(ANTHROPIC_API_KEY=None)
    _m.CREDS_FILE = "/nonexistent/creds.json"
    _expect_exit(_m._load_api_key, 2)


def test_api_key_empty_in_file_exits_2():
    _set_env(ANTHROPIC_API_KEY=None)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "creds.json")
        json.dump({"ANTHROPIC_API_KEY": "   "}, open(p, "w"))
        _m.CREDS_FILE = p
        _expect_exit(_m._load_api_key, 2)


# --- _load_auth_context: the premium-access security gate ----------------------

def test_auth_context_missing_denies():
    _set_env(CHAD_AUTH_CONTEXT_PATH=None)
    _expect_exit(_m._load_auth_context, 1)


def test_auth_context_allows_premium_true():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "ctx.json")
        json.dump({"allowsPremium": True, "source": "terminal", "verifiedIdentity": "op"}, open(p, "w"))
        _set_env(CHAD_AUTH_CONTEXT_PATH=p)
        try:
            ctx = _m._load_auth_context()
            assert ctx["allowsPremium"] is True and ctx["source"] == "terminal"
        finally:
            _set_env(CHAD_AUTH_CONTEXT_PATH=None)


def test_auth_context_allows_premium_false_denies():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "ctx.json")
        json.dump({"allowsPremium": False, "source": "gh-mention"}, open(p, "w"))
        _set_env(CHAD_AUTH_CONTEXT_PATH=p)
        try:
            _expect_exit(_m._load_auth_context, 1)
        finally:
            _set_env(CHAD_AUTH_CONTEXT_PATH=None)


def test_auth_context_malformed_denies_not_crash():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "ctx.json")
        open(p, "w").write("{ broken")
        _set_env(CHAD_AUTH_CONTEXT_PATH=p)
        try:
            _expect_exit(_m._load_auth_context, 1)
        finally:
            _set_env(CHAD_AUTH_CONTEXT_PATH=None)


# --- helpers -------------------------------------------------------------------

def test_extract_text_concatenates_text_parts_only():
    resp = {"content": [
        {"type": "text", "text": "Hello "},
        {"type": "tool_use", "text": "IGNORED"},
        {"type": "text", "text": "world"},
    ]}
    assert _m._extract_text(resp) == "Hello world"
    assert _m._extract_text({}) == ""  # no content key


def test_audit_appends_jsonl_and_never_raises():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "audit.jsonl")
        _m.AUDIT_FILE = p
        _m._audit({"kind": "probe", "ok": True})
        _m._audit({"kind": "messages"})
        lines = open(p).read().splitlines()
        assert len(lines) == 2 and json.loads(lines[0])["kind"] == "probe"
    # Un-writable path must be swallowed, not raised.
    _m.AUDIT_FILE = "/nonexistent/dir/audit.jsonl"
    _m._audit({"kind": "x"})  # no exception == pass


def _main() -> int:
    tests = sorted((n, f) for n, f in globals().items() if n.startswith("test_") and callable(f))
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as e:  # noqa: BLE001
            failures.append((name, e))
            print(f"  FAIL {name}: {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
