# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the operator allowlist gate in ``chad-shim.py``.

This is the security boundary that decides who reaches the full Chad agent, and
it is also the clearest example of the OpenShell-vault "env-first" credential
pattern (env → credentials.json → state file, first non-empty wins). Both
behaviours are load-bearing:

  * empty everywhere == fail-open (allow everyone) — an intentional, documented
    backward-compatible default, so a test pins it so it is never silently
    tightened/loosened by accident;
  * env takes precedence over the creds file, which takes precedence over the
    durable state file — the ordering the vault migration depends on.

Runs standalone (``python3 …``) or under pytest; only stdlib + the module itself.
"""
import importlib.util
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

_MOD_PATH = Path(__file__).with_name("chad-shim.py")

_spec = importlib.util.spec_from_file_location("chad_shim_under_test", _MOD_PATH)
_shim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shim)

# Env keys the resolver reads; we fully control them each call so the host's real
# /sandbox paths never leak in.
_KEYS = ("CHAD_OPERATOR_ALLOWLIST", "CHAD_CREDENTIALS", "CHAD_OPERATOR_ALLOWLIST_FILE")


@contextmanager
def _env(**kw):
    saved = {k: os.environ.get(k) for k in _KEYS}
    try:
        for k in _KEYS:
            os.environ.pop(k, None)
        for k, v in kw.items():
            if v is not None:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _load(**kw):
    # Point the file sources at nonexistent temp paths by default so an unset
    # source is a deterministic miss rather than reading real /sandbox files.
    kw.setdefault("CHAD_CREDENTIALS", "/nonexistent/creds.json")
    kw.setdefault("CHAD_OPERATOR_ALLOWLIST_FILE", "/nonexistent/allow")
    with _env(**kw):
        return _shim._load_operator_allowlist()


# --- fail-open default (must stay intentional) ---------------------------------

def test_empty_everywhere_is_fail_open():
    assert _load(CHAD_OPERATOR_ALLOWLIST="") == set()


# --- env source ----------------------------------------------------------------

def test_env_comma_string_parsed_and_normalized():
    assert _load(CHAD_OPERATOR_ALLOWLIST="  Alice@Example.COM , bob@x.co ,") == {
        "alice@example.com",
        "bob@x.co",
    }


def test_env_json_list_parsed():
    # The resolver only json-decodes the creds-file value; a JSON-looking env
    # string is treated as a comma list. A single email still resolves cleanly.
    assert _load(CHAD_OPERATOR_ALLOWLIST="solo@op.co") == {"solo@op.co"}


def test_env_blanks_and_dupes_collapsed():
    assert _load(CHAD_OPERATOR_ALLOWLIST="a@b.co, ,a@b.co, A@B.CO") == {"a@b.co"}


# --- credentials.json fallback (env empty) -------------------------------------

def test_creds_json_string_value():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "creds.json")
        json.dump({"CHAD_OPERATOR_ALLOWLIST": "op1@x.co,op2@x.co"}, open(p, "w"))
        assert _load(CHAD_CREDENTIALS=p) == {"op1@x.co", "op2@x.co"}


def test_creds_json_list_value():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "creds.json")
        json.dump({"CHAD_OPERATOR_ALLOWLIST": ["A@x.co", "b@x.co"]}, open(p, "w"))
        assert _load(CHAD_CREDENTIALS=p) == {"a@x.co", "b@x.co"}


def test_malformed_creds_json_is_ignored_not_crash():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "creds.json")
        open(p, "w").write("{ not valid json")
        assert _load(CHAD_CREDENTIALS=p) == set()  # falls through, no exception


# --- state-file fallback (env + creds empty) -----------------------------------

def test_file_newlines_commas_comments_blanks():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "allow")
        open(p, "w").write("# operators\nalice@x.co\n\nbob@x.co, carol@x.co\n  # trailing\n")
        assert _load(CHAD_OPERATOR_ALLOWLIST_FILE=p) == {
            "alice@x.co",
            "bob@x.co",
            "carol@x.co",
        }


# --- precedence (the vault-migration ordering) ---------------------------------

def test_env_wins_over_creds_and_file():
    with tempfile.TemporaryDirectory() as d:
        creds = os.path.join(d, "creds.json")
        allow = os.path.join(d, "allow")
        json.dump({"CHAD_OPERATOR_ALLOWLIST": "fromcreds@x.co"}, open(creds, "w"))
        open(allow, "w").write("fromfile@x.co\n")
        assert _load(
            CHAD_OPERATOR_ALLOWLIST="fromenv@x.co",
            CHAD_CREDENTIALS=creds,
            CHAD_OPERATOR_ALLOWLIST_FILE=allow,
        ) == {"fromenv@x.co"}


def test_creds_wins_over_file():
    with tempfile.TemporaryDirectory() as d:
        creds = os.path.join(d, "creds.json")
        allow = os.path.join(d, "allow")
        json.dump({"CHAD_OPERATOR_ALLOWLIST": "fromcreds@x.co"}, open(creds, "w"))
        open(allow, "w").write("fromfile@x.co\n")
        assert _load(CHAD_CREDENTIALS=creds, CHAD_OPERATOR_ALLOWLIST_FILE=allow) == {
            "fromcreds@x.co"
        }


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
