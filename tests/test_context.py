"""build_system / build_messages: tool summaries and cache-friendly context."""

import pytest

import consciousness_server as cs


@pytest.fixture(autouse=True)
def _prompts(monkeypatch):
    monkeypatch.setattr(cs, "SYSTEM_PROMPT", "BASE PROMPT")
    monkeypatch.setattr(cs, "CYCLE_PROMPTS", {})


def _seg(cycle, content, tools=None, user=None):
    return {
        "cycle": cycle,
        "content": content,
        "toolActions": [{"tool": t, "input": {}, "result": {}} for t in tools] if tools else None,
        "userMessage": user,
    }


def _state(**kw):
    state = {
        "memoryCore": "CORE TEXT",
        "insights": "INSIGHTS TEXT",
        "workingMemory": {"operational_practices": "PRACTICES", "todo": "TODO TEXT"},
        "segments": [
            _seg(1, "old long " + "y" * 800, tools=["read_file"]),
            _seg(2, "short a", tools=["write_scratchpad", "read_file"]),
            _seg(3, "short b"),
            _seg(4, "", tools=["finish_thought"]),
            _seg(5, ""),
            _seg(6, "short c", tools=["search_files"]),
            _seg(7, "short d", tools=["list_scratchpad"]),
        ],
        "totalCycles": 7,
    }
    state.update(kw)
    return state


def test_every_segment_with_tools_gets_summary():
    msgs = cs.build_messages(_state())
    assistant = [m["content"] for m in msgs if m["role"] == "assistant"]
    assert len(assistant) == 7
    # Masked old segment: truncated, then tools.
    assert "[...truncated...]" in assistant[0]
    assert assistant[0].endswith("[Tools used: read_file]")
    # Recent (unmasked) segments also get it, in call order.
    assert assistant[1] == "short a\n[Tools used: write_scratchpad, read_file]"
    assert assistant[2] == "short b"
    assert assistant[3] == "[Tools used: finish_thought]"
    assert assistant[4] == "(no text output)"  # never send an empty turn
    assert assistant[6].endswith("[Tools used: list_scratchpad]")


def test_volatile_content_only_in_last_user_message():
    state = _state(
        privacyMode=True,
        privacyStartCycle=3,
        privacyCyclesElapsed=4,
        queuedMessages=[{"timestamp": "t0", "message": "QUEUED HELLO"}],
        _privacyNudge=True,
    )
    system = cs.build_system(state)
    msgs = cs.build_messages(state)
    last = msgs[-1]

    assert last["role"] == "user"
    assert last["content"].startswith("[Cycle 8")
    for needle in ("QUEUED HELLO", "Privacy mode active", "4 cycles in privacy mode",
                   "reconnect when you're ready"):
        assert needle in last["content"]
        assert needle not in system
        assert all(needle not in m["content"] for m in msgs[:-1]
                   if isinstance(m["content"], str))
    # Queued messages are not consumed by building the prompt.
    assert state["queuedMessages"]


def test_system_prompt_is_stable_and_ordered(monkeypatch):
    state = _state()
    first = cs.build_system(state)

    class LaterDatetime(cs.datetime):
        @classmethod
        def now(cls, tz=None):
            return cs.datetime(2031, 1, 2, 3, 4)

    monkeypatch.setattr(cs, "datetime", LaterDatetime)
    assert cs.build_system(state) == first
    assert "2031" in cs.build_messages(state)[-1]["content"]

    order = [first.index(x) for x in
             ("BASE PROMPT", "CORE TEXT", "INSIGHTS TEXT", "PRACTICES", "TODO TEXT")]
    assert order == sorted(order)


def test_interjection_gets_volatile_context():
    state = _state(queuedMessages=[{"timestamp": "t", "message": "Q"}])
    msgs = cs.build_messages(state, user_interjection="hello from Alex")
    assert msgs[-1]["content"].startswith("hello from Alex")
    assert "Messages from Alex (1)" in msgs[-1]["content"]
