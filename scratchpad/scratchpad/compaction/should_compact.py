"""
Compaction trigger logic — determines WHEN compaction should fire.

Contract:
    should_compact(state, segments, config) -> (bool, str|None)
    Returns (True/False, reason_string_or_None)

Claude can edit this file to change compaction triggers.
Changes take effect on the next cycle (hot-reloaded).

Available data in each segment dict:
    - cycle: int (cycle number)
    - content: str (the thought text)
    - timestamp: str (ISO format, if present)
    - toolActions: list (tools called during this cycle)
    - stopReason: str ("end_turn" or "tool_use")
"""


def estimate_tokens(text):
    return len(text) // 4


def should_compact(state, segments, config):
    """
    Decide whether compaction should trigger.

    Default: pure token threshold (original behavior).
    Claude can add additional triggers here.
    """
    token_threshold = config.get("compactionTokenThreshold", 50000)

    total_segment_tokens = sum(
        estimate_tokens(s.get("content", "")) for s in segments
    )

    if total_segment_tokens >= token_threshold:
        return True, f"token_threshold ({total_segment_tokens} >= {token_threshold})"

    return False, None
