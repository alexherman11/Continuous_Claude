"""
Segment selection logic — determines WHAT gets compacted.

Contract:
    select_segments(segments, config) -> (to_compact, remaining)
    Returns two lists: segments to compact, segments to keep.
    MUST keep at least 1 segment in 'remaining' (never compact everything).

Claude can edit this file to change selection strategy.
Changes take effect on the next cycle (hot-reloaded).
"""


def estimate_tokens(text):
    return len(text) // 4


def select_segments(segments, config):
    """
    Select which segments to compact.

    Default: oldest segments up to tokensToCompact budget (original behavior).
    Claude can change this to e.g. select by topic, skip important segments, etc.
    """
    tokens_to_compact = config.get("tokensToCompact", 10000)
    token_threshold = config.get("compactionTokenThreshold", 50000)

    # Enforce: tokensToCompact <= 20% of token_threshold
    max_compact = int(token_threshold * 0.2)
    if tokens_to_compact > max_compact:
        tokens_to_compact = max_compact

    # Accumulate oldest segments up to token budget
    accumulated = 0
    count = 0
    for seg in segments:
        content = seg.get("content", "") if isinstance(seg, dict) else getattr(seg, "content", "")
        seg_tokens = estimate_tokens(content)
        if accumulated + seg_tokens > tokens_to_compact and count > 0:
            break
        accumulated += seg_tokens
        count += 1

    count = max(1, count)

    # Never compact ALL segments
    count = min(count, len(segments) - 1)

    return segments[:count], segments[count:]
