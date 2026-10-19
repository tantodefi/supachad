# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Offline tests for chad-dream-distill's merge / consolidate / render logic.

The LLM is replaced by a stub, so this runs anywhere: ``python3 …`` or pytest.
"""
import importlib.machinery
import importlib.util
import json
from pathlib import Path

_path = str(Path(__file__).with_name("chad-dream-distill"))
_loader = importlib.machinery.SourceFileLoader("chad_dream_distill_under_test", _path)
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
d = importlib.util.module_from_spec(_spec)
_loader.exec_module(d)


def test_parse_extracts_json_and_caps():
    reply = 'noise {"profile": ["a", "", "' + "x" * 300 + '"], "decisions": "not a list"} tail'
    out = d.parse(reply)
    assert out["profile"] == ["a", "x" * 160]
    assert out["decisions"] == []


def test_parse_garbage_is_empty():
    assert d.parse("") == {}
    assert d.parse("no json here") == {}


def test_merge_dedupes_normalized_text():
    store = d.merge({}, {"profile": ["Runs a gym.", "runs a gym"]}, "2026-10-19")
    assert [x["text"] for x in store["profile"]] == ["Runs a gym."]


def test_render_empty_store_is_empty():
    assert d.render("tj", {}) == ""
    assert d.render("tj", {"profile": []}) == ""


def test_render_caps_each_category():
    store = {k: [{"text": f"{k}-{i}", "date": "2026-10-19"} for i in range(20)] for k in d.CATEGORIES}
    md = d.render("tj", store)
    for k in d.CATEGORIES:  # every category survives; none crowds the others out
        assert f"{k}-19" in md
        assert md.count(f"- {k}-") <= d.RENDER_PER_CATEGORY
    assert len(md.encode()) <= d.CONTEXT_MAX_BYTES


def test_consolidate_skips_small_store():
    calls = []
    d.llm = lambda p: calls.append(p) or ""
    store = {"profile": [{"text": "a", "date": "2026-10-01"}]}
    assert d.consolidate("tj", store) is store
    assert calls == []


def test_consolidate_keeps_store_on_failure():
    d.llm = lambda p: ""
    store = {"profile": [{"text": f"p{i}", "date": "2026-10-01"} for i in range(12)]}
    assert d.consolidate("tj", store) is store


def test_consolidate_orders_and_keeps_dates():
    store = {"profile": [{"text": f"p{i}", "date": f"2026-10-{i + 1:02d}"} for i in range(12)]}
    d.llm = lambda p: json.dumps({"profile": ["p3", "merged new"]})
    out = d.consolidate("tj", store)
    texts = [x["text"] for x in out["profile"]]
    assert texts == ["merged new", "p3"]  # stored oldest-first; render reverses
    assert out["profile"][1]["date"] == "2026-10-04"  # original date kept
    assert out["profile"][0]["date"] == "2026-10-12"  # new wording gets newest date
    md = d.render("tj", out)
    assert md.index("- p3") < md.index("- merged new")  # render shows the model's order


def _main():
    tests = sorted((n, f) for n, f in globals().items() if n.startswith("test_") and callable(f))
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as e:  # noqa: BLE001
            failures.append((name, e))
            print(f"  FAIL {name}: {e!r}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
