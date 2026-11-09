"""
Validation for Core Identity rewrites produced by compaction integration.

Background: integration used to run with max_tokens=2000 while asking for
a ~16000-char core (~5300 tokens). Every rewrite hit max_tokens and the
cut-off text was saved as the new core, so the core silently froze at a
truncated length for 20+ versions. These helpers make that failure loud:
a truncated or degenerate rewrite is rejected and the old core is kept.

Shared by consciousness_server.py and continuous_consciousness.py.
"""

# Substrings that indicate the text was cut or copied from masked history.
TRUNCATION_MARKERS = ("[...truncated", "[…truncated", "[truncated]")

# A new core shorter than this fraction of the old one is treated as lossy.
MIN_CORE_RATIO = 0.6


def effective_integration_max_tokens(configured, core_max_chars):
    """
    max_tokens for the integration call: enough room for a full core.

    ~3 chars/token is a conservative estimate for prose (Claude averages
    closer to 4), plus 500 tokens of headroom.
    """
    try:
        configured = int(configured)
    except (TypeError, ValueError):
        configured = 0
    return max(configured, int(core_max_chars) // 3 + 500)


def check_stop_reason(stop_reason):
    """Return a rejection reason if the response was cut off, else None."""
    if stop_reason == "max_tokens":
        return "stop_reason=max_tokens (output truncated)"
    return None


def validate_new_core(new_core, old_core, stop_reason=None, min_ratio=MIN_CORE_RATIO):
    """
    Decide whether an integration result may replace the current core.

    Returns (ok: bool, reason: str | None).
    """
    reason = check_stop_reason(stop_reason)
    if reason:
        return False, reason
    if not new_core or not new_core.strip():
        return False, "empty core"
    for marker in TRUNCATION_MARKERS:
        if marker in new_core:
            return False, f"contains truncation marker {marker!r}"
    if old_core and old_core.strip():
        if len(new_core) < min_ratio * len(old_core):
            return False, (
                f"new core too short ({len(new_core)} chars < "
                f"{min_ratio:.0%} of old {len(old_core)} chars)"
            )
    return True, None
