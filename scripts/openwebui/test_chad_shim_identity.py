# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for per-operator identity + distilled context loading in ``chad-shim.py``.

chad-dream-distill writes ``<slug>.context.md`` next to the hand-written
``<slug>.md``. The shim appends it on every turn — but only on top of a real
identity. An operator with no identity file (e.g. a new paying user) must still
get "" so the caller falls back to ``default.md`` and its restricted trust rules.

Runs standalone (``python3 …``) or under pytest; only stdlib + the module itself.
"""
import importlib.util
import tempfile
from pathlib import Path

_MOD_PATH = Path(__file__).with_name("chad-shim.py")
_spec = importlib.util.spec_from_file_location("chad_shim_identity_under_test", _MOD_PATH)
_shim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shim)


def _with_dir(files):
    d = tempfile.mkdtemp()
    for name, text in files.items():
        Path(d, name).write_text(text, encoding="utf-8")
    _shim.IDENTITY_DIR = d
    _shim._identity_cache.clear()
    return d


def test_identity_only():
    _with_dir({"tanto.md": "# Operator: tanto"})
    assert _shim._load_identity("tanto") == "# Operator: tanto"


def test_identity_plus_context():
    _with_dir({"tanto.md": "# Operator: tanto", "tanto.context.md": "## learned"})
    assert _shim._load_identity("tanto") == "# Operator: tanto\n\n## learned"


def test_context_without_identity_falls_back():
    # Security: context alone must not count as an identity, or the caller would
    # skip default.md's restricted rules for an unknown operator.
    _with_dir({"stranger.context.md": "## learned"})
    assert _shim._load_identity("stranger") == ""


def test_empty_slug():
    _with_dir({"default.md": "x"})
    assert _shim._load_identity("") == ""


def test_context_change_is_picked_up():
    d = _with_dir({"tanto.md": "id", "tanto.context.md": "v1"})
    assert _shim._load_identity("tanto").endswith("v1")
    p = Path(d, "tanto.context.md")
    p.write_text("v2", encoding="utf-8")
    import os

    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 5))  # force a new mtime
    assert _shim._load_identity("tanto").endswith("v2")


def _main():
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
