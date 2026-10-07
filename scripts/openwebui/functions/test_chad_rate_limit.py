# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Chad Lite rate-limit / credit-metering OpenWebUI filter.

Covers the billing-critical paths of ``chad_rate_limit.py``: premium/admin
exemption, the free-tier daily cap, UTC day rollover, Square credit consumption
(skips the cap while in balance), and graceful fall-through to the free cap when
the credits Worker is unconfigured or unreachable (an outage must never hard-block).

Runs two ways, with no new dependencies beyond pydantic (already in the OWUI image):
    python3 scripts/openwebui/functions/test_chad_rate_limit.py   # standalone
    pytest  scripts/openwebui/functions/test_chad_rate_limit.py   # if pytest present

The module under test is loaded by path via importlib (mirroring
browser-vm/test/test_e2e.py), so cwd does not matter.
"""
import asyncio
import importlib.util
import os
import tempfile
from pathlib import Path

_MOD_PATH = Path(__file__).with_name("chad_rate_limit.py")


def _fresh_module(state_path: str):
    """Load a fresh copy of the filter module with its state file pointed at a
    temp path. Returns the module object; each test gets an isolated instance so
    module-level globals (_STATE_PATH, _CREDITS_SECRET, _consume_credit) can be
    monkeypatched without cross-test leakage."""
    spec = importlib.util.spec_from_file_location("chad_rate_limit_under_test", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._STATE_PATH = state_path
    mod._CREDITS_SECRET = ""  # default: credits disabled (free-cap only)
    return mod


def _run(coro):
    return asyncio.run(coro)


def _inlet(mod, *, model="nemotron", user=None):
    return _run(mod.Filter().inlet({"model": model}, __user__=user))


# --- exemption matching (premium models + admins bypass the cap) ---------------

def test_exempt_models_match_variants():
    with tempfile.TemporaryDirectory() as d:
        f = _fresh_module(os.path.join(d, "s.json")).Filter()
        assert f._exempt("chad") is True
        assert f._exempt("openai.chad") is True          # OWUI connection-id prefix
        assert f._exempt("nvidia/chad") is True           # path-segment form
        assert f._exempt("gpt-4") is False
        assert f._exempt("chad-lite") is False            # substring must not match


def test_admin_is_exempt_and_not_counted():
    with tempfile.TemporaryDirectory() as d:
        sp = os.path.join(d, "s.json")
        mod = _fresh_module(sp)
        body = _inlet(mod, user={"role": "admin", "email": "a@b.co", "id": "u1"})
        assert body == {"model": "nemotron"}
        assert not os.path.exists(sp)  # exempt path never writes state


def test_exempt_model_bypasses_before_user_checks():
    with tempfile.TemporaryDirectory() as d:
        mod = _fresh_module(os.path.join(d, "s.json"))
        # No user at all: exempt model must still pass (no KeyError on __user__).
        assert _inlet(mod, model="chad", user=None) == {"model": "chad"}


# --- free-tier daily cap -------------------------------------------------------

def test_free_cap_allows_under_limit_and_increments():
    with tempfile.TemporaryDirectory() as d:
        sp = os.path.join(d, "s.json")
        mod = _fresh_module(sp)
        mod.Filter().valves.free_daily_limit  # sanity: valves load
        f = mod.Filter()
        f.valves.free_daily_limit = 3
        for _ in range(3):
            assert _run(f.inlet({"model": "nemotron"}, __user__={"id": "u1"})) == {"model": "nemotron"}
        import json
        counts = json.load(open(sp))["counts"]
        assert counts["u1"] == 3


def test_free_cap_blocks_at_limit_with_upgrade_cta():
    with tempfile.TemporaryDirectory() as d:
        mod = _fresh_module(os.path.join(d, "s.json"))
        f = mod.Filter()
        f.valves.free_daily_limit = 2
        _run(f.inlet({"model": "nemotron"}, __user__={"id": "u1"}))
        _run(f.inlet({"model": "nemotron"}, __user__={"id": "u1"}))
        try:
            _run(f.inlet({"model": "nemotron"}, __user__={"id": "u1"}))
            assert False, "expected the cap to raise"
        except Exception as e:
            msg = str(e)
            assert "limit" in msg.lower() and "supachad.com" in msg
            assert "2 messages" in msg  # {limit} substituted


def test_per_user_isolation():
    with tempfile.TemporaryDirectory() as d:
        mod = _fresh_module(os.path.join(d, "s.json"))
        f = mod.Filter()
        f.valves.free_daily_limit = 1
        _run(f.inlet({"model": "nemotron"}, __user__={"id": "u1"}))
        # u2 has its own budget — not blocked by u1 exhausting theirs.
        assert _run(f.inlet({"model": "nemotron"}, __user__={"id": "u2"})) == {"model": "nemotron"}


def test_day_rollover_resets_counts():
    with tempfile.TemporaryDirectory() as d:
        sp = os.path.join(d, "s.json")
        import json
        json.dump({"day": "2000-01-01", "counts": {"u1": 999}}, open(sp, "w"))
        mod = _fresh_module(sp)
        f = mod.Filter()
        f.valves.free_daily_limit = 5
        # Stale day → counts reset, so this must pass despite the 999.
        assert _run(f.inlet({"model": "nemotron"}, __user__={"id": "u1"})) == {"model": "nemotron"}
        assert json.load(open(sp))["counts"]["u1"] == 1


# --- Square credit metering ----------------------------------------------------

def test_credit_balance_skips_daily_cap():
    with tempfile.TemporaryDirectory() as d:
        mod = _fresh_module(os.path.join(d, "s.json"))
        mod._CREDITS_SECRET = "secret"
        mod._consume_credit = lambda email, amount=1: ("ok", 42)
        f = mod.Filter()
        f.valves.free_daily_limit = 0  # free cap would block everyone...
        # ...but a credit-metered user in balance sails past it.
        assert _run(f.inlet({"model": "nemotron"}, __user__={"id": "u1", "email": "a@b.co"})) == {"model": "nemotron"}


def test_insufficient_credits_falls_through_to_free_cap():
    with tempfile.TemporaryDirectory() as d:
        mod = _fresh_module(os.path.join(d, "s.json"))
        mod._CREDITS_SECRET = "secret"
        mod._consume_credit = lambda email, amount=1: ("insufficient", 0)
        f = mod.Filter()
        f.valves.free_daily_limit = 1
        assert _run(f.inlet({"model": "nemotron"}, __user__={"id": "u1", "email": "a@b.co"})) == {"model": "nemotron"}
        try:
            _run(f.inlet({"model": "nemotron"}, __user__={"id": "u1", "email": "a@b.co"}))
            assert False, "expected free cap to apply after credits ran out"
        except Exception as e:
            assert "supachad.com" in str(e)


def test_worker_outage_never_hard_blocks():
    with tempfile.TemporaryDirectory() as d:
        mod = _fresh_module(os.path.join(d, "s.json"))
        mod._CREDITS_SECRET = "secret"
        mod._consume_credit = lambda email, amount=1: ("error", 0)  # Worker unreachable
        f = mod.Filter()
        f.valves.free_daily_limit = 5
        # Outage → fall through to the free cap, which still has budget → allow.
        assert _run(f.inlet({"model": "nemotron"}, __user__={"id": "u1", "email": "a@b.co"})) == {"model": "nemotron"}


def test_consume_credit_unconfigured_without_secret():
    with tempfile.TemporaryDirectory() as d:
        mod = _fresh_module(os.path.join(d, "s.json"))
        # No secret / bad email → ("unconfigured", 0), no network attempted.
        assert mod._consume_credit("a@b.co")[0] == "unconfigured"
        mod._CREDITS_SECRET = "secret"
        assert mod._consume_credit("")[0] == "unconfigured"
        assert mod._consume_credit("not-an-email")[0] == "unconfigured"


# --- standalone runner (no pytest needed) --------------------------------------

def _main() -> int:
    tests = sorted(
        (n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)
    )
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as e:  # noqa: BLE001 — test harness reports all failures
            failures.append((name, e))
            print(f"  FAIL {name}: {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
