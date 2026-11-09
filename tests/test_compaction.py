"""Compaction: plugin output validation and Core Identity integration safety."""

import json
import os
from types import SimpleNamespace

import pytest

import consciousness_server as cs
from file_manager import FileManager


def _resp(text, stop_reason="end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """consciousness_server with all side effects redirected to tmp_path."""
    monkeypatch.setattr(cs, "file_mgr", FileManager(base_path=str(tmp_path)))
    monkeypatch.setattr(cs, "API_LOG_PATH", str(tmp_path / "api_calls.log"))
    monkeypatch.setattr(cs, "COMPACTION_PLUGINS", {})
    monkeypatch.setattr(cs, "CYCLE_PROMPTS", {})
    events = tmp_path / "events.jsonl"
    monkeypatch.setenv("CC_EVENT_LOG_PATH", str(events))

    def install(responses):
        fake = FakeMessages(responses)
        monkeypatch.setattr(cs, "client", SimpleNamespace(messages=fake))
        return fake

    def read_events():
        if not events.exists():
            return []
        return [json.loads(l) for l in events.read_text().splitlines()]

    return SimpleNamespace(install=install, events=read_events, tmp=tmp_path)


STAGED = "### Compaction #1 (Cycles 1-2)\n\nOLD-INSIGHT-ALPHA staged for integration"
INSIGHTS = STAGED + "\n\n### Compaction #2 (Cycles 3-4)\n\nRECENT-INSIGHT-BETA"


def _state(core="", insights=INSIGHTS, n_segments=4):
    return {
        "memoryCore": core,
        "insights": insights,
        "segments": [
            {"cycle": i, "content": f"thought {i} " + "x" * 200, "toolActions": None}
            for i in range(1, n_segments + 1)
        ],
        "totalCycles": n_segments,
        "totalCompactions": 2,
        "config": {
            "compactionTokenThreshold": 10,
            "tokensToCompact": 100,
            "insightsMaxChars": 50,   # force integration
            "insightsOverflowPercent": 0.3,
            "coreMaxChars": 16000,
        },
    }


# ── Integration rejection ───────────────────────────────────────────

def test_integration_rejected_on_max_tokens(engine):
    old_core = "CORE " * 400
    fake = engine.install([
        _resp("NEW-EXTRACTED-INSIGHT"),
        _resp("truncated new core that stops mid-wo", stop_reason="max_tokens"),
    ])
    state = _state(core=old_core)

    assert cs.run_compaction(state) is True

    assert state["memoryCore"] == old_core
    # Staged insights restored (still present), plus the new extraction.
    assert "OLD-INSIGHT-ALPHA" in state["insights"]
    assert "RECENT-INSIGHT-BETA" in state["insights"]
    assert "NEW-EXTRACTED-INSIGHT" in state["insights"]

    # Integration was given room for a full core.
    assert fake.calls[1]["max_tokens"] >= 16000 // 3 + 500

    ev = [e for e in engine.events() if e["event"] == "compaction_integration"]
    assert ev and ev[-1]["accepted"] is False
    assert "max_tokens" in ev[-1]["reject_reason"]
    assert ev[-1]["core_chars_before"] == len(old_core)


def test_integration_rejected_when_much_shorter(engine):
    old_core = "A" * 1000
    engine.install([_resp("insight"), _resp("B" * 100)])
    state = _state(core=old_core)
    cs.run_compaction(state)
    assert state["memoryCore"] == old_core
    assert "OLD-INSIGHT-ALPHA" in state["insights"]


def test_integration_rejected_on_truncation_marker(engine):
    old_core = "A" * 1000
    engine.install([_resp("insight"), _resp("C" * 1000 + "\n[...truncated...]")])
    state = _state(core=old_core)
    cs.run_compaction(state)
    assert state["memoryCore"] == old_core
    assert "OLD-INSIGHT-ALPHA" in state["insights"]


def test_integration_rejected_when_empty(engine):
    engine.install([_resp("insight"), _resp("   ")])
    state = _state(core="")
    cs.run_compaction(state)
    assert state["memoryCore"] == ""
    assert "OLD-INSIGHT-ALPHA" in state["insights"]


def test_integration_accepted(engine):
    old_core = "A" * 1000
    new_core = "D" * 900
    engine.install([_resp("insight"), _resp(new_core)])
    state = _state(core=old_core)
    cs.run_compaction(state)
    assert state["memoryCore"] == new_core
    assert "OLD-INSIGHT-ALPHA" not in state["insights"]
    assert "RECENT-INSIGHT-BETA" in state["insights"]
    ev = [e for e in engine.events() if e["event"] == "compaction_integration"]
    assert ev[-1]["accepted"] is True


def test_truncated_extraction_not_appended(engine):
    engine.install([_resp("HALF-AN-INSIGHT", stop_reason="max_tokens")])
    state = _state(insights="short")  # below insightsMaxChars after this
    state["config"]["insightsMaxChars"] = 10_000
    cs.run_compaction(state)
    assert "HALF-AN-INSIGHT" not in state["insights"]
    ev = [e for e in engine.events() if e["event"] == "compaction_extraction"]
    assert ev[-1]["accepted"] is False
    assert ev[-1]["rejected_text"] == "HALF-AN-INSIGHT"


def test_compaction_calls_are_logged(engine):
    engine.install([_resp("insight"), _resp("D" * 900)])
    cs.run_compaction(_state(core="A" * 1000))
    log = (engine.tmp / "api_calls.log").read_text()
    assert "compaction-extraction" in log
    assert "compaction-integration" in log


# ── Plugin output validation ────────────────────────────────────────

def test_select_segments_empty_output_falls_back(engine, monkeypatch):
    monkeypatch.setattr(cs, "COMPACTION_PLUGINS", {
        "select_segments": lambda segments, config: ([], segments),
    })
    engine.install([_resp("insight")])
    state = _state(insights="")
    state["config"]["insightsMaxChars"] = 10_000
    before = len(state["segments"])

    assert cs.run_compaction(state) is True
    assert len(state["segments"]) < before  # default selection was used

    fb = [e for e in engine.events() if e["event"] == "compaction_plugin_fallback"]
    assert fb and fb[-1]["plugin"] == "select_segments"
    assert "invalid output" in fb[-1]["reason"]


def test_select_segments_reordered_output_falls_back(engine, monkeypatch):
    monkeypatch.setattr(cs, "COMPACTION_PLUGINS", {
        "select_segments": lambda segments, config: ([segments[2]], segments[:2] + segments[3:]),
    })
    engine.install([_resp("insight")])
    state = _state(insights="")
    state["config"]["insightsMaxChars"] = 10_000
    cs.run_compaction(state)
    # Default compacts from the front, so cycle 1 is gone and 3 remains.
    remaining = [s["cycle"] for s in state["segments"]]
    assert 1 not in remaining and 3 in remaining


def test_should_compact_wrong_type_falls_back(engine, monkeypatch):
    monkeypatch.setattr(cs, "COMPACTION_PLUGINS", {
        "should_compact": lambda state, segments, config: "yes",
    })
    engine.install([_resp("insight")])
    state = _state(insights="")
    state["config"]["insightsMaxChars"] = 10_000
    assert cs.run_compaction(state) is True  # default triggers on threshold


def test_format_and_post_process_non_str_fall_back(engine, monkeypatch):
    monkeypatch.setattr(cs, "COMPACTION_PLUGINS", {
        "format_segments": lambda to_compact, state: None,
        "post_process": lambda raw, n, cycles, state: ["not", "a", "str"],
    })
    fake = engine.install([_resp("INSIGHT-X")])
    state = _state(insights="")
    state["config"]["insightsMaxChars"] = 10_000
    cs.run_compaction(state)
    assert "[Cycle 1]" in fake.calls[0]["messages"][0]["content"]
    assert state["insights"].startswith("### Compaction #3")
    plugins = {e["plugin"] for e in engine.events() if e["event"] == "compaction_plugin_fallback"}
    assert {"format_segments", "post_process"} <= plugins


def test_plugin_exception_falls_back(engine, monkeypatch):
    def boom(*a):
        raise RuntimeError("plugin bug")
    monkeypatch.setattr(cs, "COMPACTION_PLUGINS", {"select_segments": boom})
    engine.install([_resp("insight")])
    state = _state(insights="")
    state["config"]["insightsMaxChars"] = 10_000
    assert cs.run_compaction(state) is True


@pytest.mark.parametrize("out,ok", [
    ((True, "why"), True),
    ((False, None), True),
    (("True", None), False),
    ((True,), False),
    ((True, 5), False),
])
def test_validate_should_compact(out, ok):
    assert (cs._validate_should_compact(out, None) is None) == ok
