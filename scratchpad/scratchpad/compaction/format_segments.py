"""
Segment formatting logic — determines HOW segments are presented to the extraction LLM.

Contract:
    format_segments(to_compact, state) -> str
    Returns formatted text that will be inserted into the extraction prompt
    as the "SEGMENTS BEING COMPACTED" section.

Claude can edit this file to change how segments are presented for extraction.
Changes take effect on the next cycle (hot-reloaded).

This is where you can add metadata like cycle ranges, timestamps, tool usage
summaries, or any other context that helps the extraction LLM produce
better insights.
"""


def format_segments(to_compact, state):
    """
    Format segments for the extraction prompt.

    Default: [Cycle N] content, separated by --- dividers.
    Claude can enrich this with timestamps, tool summaries, topic headers, etc.
    """
    cycle_nums = [s.get("cycle", "?") for s in to_compact]
    cycle_range = f"Cycles {cycle_nums[0]}-{cycle_nums[-1]}"

    parts = [f"[Compacting {cycle_range} — {len(to_compact)} segments]"]
    parts.append("")

    for seg in to_compact:
        cycle = seg.get("cycle", "?")
        content = seg.get("content", "")
        timestamp = seg.get("timestamp", "")

        header = f"[Cycle {cycle}]"
        if timestamp:
            header += f" ({timestamp})"

        # Summarize tool usage if present
        tool_actions = seg.get("toolActions", [])
        if tool_actions:
            tool_names = [a.get("tool", "unknown") for a in tool_actions if isinstance(a, dict)]
            if tool_names:
                header += f" [tools: {', '.join(tool_names)}]"

        parts.append(header)
        parts.append(content)
        parts.append("")
        parts.append("---")
        parts.append("")

    return "\n".join(parts)
