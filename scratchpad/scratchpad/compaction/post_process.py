"""
Post-processing logic — structures the raw LLM extraction output before
it gets appended to the Insights layer.

Contract:
    post_process(raw_insights, compaction_num, compacted_cycles, state) -> str
    Returns the formatted insight block to append to the Insights layer.
    Return empty string or "(no new insights)" to skip appending.

Claude can edit this file to change how insights are structured and tagged.
Changes take effect on the next cycle (hot-reloaded).
"""


def post_process(raw_insights, compaction_num, compacted_cycles, state):
    """
    Format extracted insights before appending to the Insights layer.

    Default: wrap in a header with compaction number and cycle range.
    Claude can add additional metadata, restructure, re-tag, etc.
    """
    if not raw_insights or raw_insights.strip() == "(no new insights)":
        return ""

    header = f"### Compaction #{compaction_num} (Cycles {compacted_cycles[0]}-{compacted_cycles[-1]})"
    return f"{header}\n\n{raw_insights.strip()}"
