"""
===================================
Two-Layer Memory Compaction System
===================================

This is the memory management engine for a continuously-running AI system
I call Continous Claude. Inspired by a desire to allow LLM's more space for
self reflection and personal exploration, I decided to experiment with
solving the problem of finite chats. I developed my own custom compaction
architecture, inspired by OpenClaw's SOUL.md.

  Layer 1: Segments → Insights
    An LLM reads the oldest segments, extracts key insights, and discards
    the raw text. Like turning a week of journal entries into bullet points.

  Layer 2: Insights → Core Identity
    When insights pile up too high, the oldest ones get evaluated against
    a persistent "Core Identity" document. Worthy insights are woven in;
    redundant ones are dropped. Like revising your personal bio once a year.

The entire Layer 1 pipeline is pluggable — each stage lives in its own
Python file that gets hot-reloaded every cycle. If Claude (the AI running
inside this system) edits one of those files and introduces a bug, the
engine catches the error and falls back to built-in defaults. This lets
the AI experiment with its own memory strategy without risking a crash.

This is my intellectual property and I reserve all rights to its use and distribution.
"""

import os
import sys
import json
import logging
import importlib.util
import py_compile

import anthropic

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-4-20250514"

# Prompt templates — loaded from disk each cycle so edits take effect live.
# If the file is missing or unreadable, the fallback string is used instead.
EXTRACTION_PROMPT_FALLBACK = (
    "Extract significant insights from the following segments. "
    "Tag each with [IDENTITY], [BREAKTHROUGH], [RELATIONAL], [OPERATIONAL], or [QUESTION]. "
    "Be selective."
)
INTEGRATION_PROMPT_FALLBACK = (
    "Evaluate the staged insights against the current Core Identity. "
    "Integrate worthy ones, drop those already represented. "
    "Target ~3500 characters."
)

client = anthropic.Anthropic()

def estimate_tokens(text):
    """
    Quick approximation: Claude's tokenizer averages ~4 characters per token.
    Good enough for budget decisions — we don't need exact counts here.
    """
    return len(text) // 4

# ===========================================================================
#  Plugin Hot-Reload System
#
#  Each stage of the compaction pipeline can be overridden by dropping a
#  Python file into the plugins directory. The engine reloads them every
#  cycle, so changes take effect immediately — no restart needed.
#
#  If a plugin file has a syntax error or doesn't define the expected
#  function, we just skip it and use the built-in default. This makes it
#  safe for Claude to experiment with its own memory logic.
# ===========================================================================

COMPACTION_PLUGINS = {}

# Maps filename (without .py) to the function name it must export
PLUGIN_SPECS = {
    "should_compact":  "should_compact",   # (state, segments, config) -> (bool, str|None)
    "select_segments": "select_segments",  # (segments, config) -> (to_compact, remaining)
    "format_segments": "format_segments",  # (to_compact, state) -> str
    "post_process":    "post_process",     # (raw_insights, compaction_num, cycles, state) -> str
}

def reload_compaction_plugins(plugin_dir):
    """
    Scan the plugin directory, syntax-check each file, and load any valid
    functions into the COMPACTION_PLUGINS dict. Runs every cycle.
    """
    global COMPACTION_PLUGINS
    plugins = {}

    for filename_base, func_name in PLUGIN_SPECS.items():
        filepath = os.path.join(plugin_dir, f"{filename_base}.py")
        if not os.path.exists(filepath):
            continue

        try:
            # Catch syntax errors before we try to execute anything
            py_compile.compile(filepath, doraise=True)

            # Import the module fresh every time (no stale cached version)
            module_name = f"compaction_plugin.{filename_base}"
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            module = importlib.util.module_from_spec(spec)

            if module_name in sys.modules:
                del sys.modules[module_name]

            spec.loader.exec_module(module)

            func = getattr(module, func_name, None)
            if func and callable(func):
                plugins[func_name] = func
                logging.info(f"Loaded compaction plugin: {func_name}")
            else:
                logging.warning(f"Plugin {filepath} exists but doesn't define {func_name}()")

        except py_compile.PyCompileError as e:
            logging.error(f"Syntax error in {filepath}: {e}")
        except Exception as e:
            logging.error(f"Failed to load plugin {filepath}: {e}")

    COMPACTION_PLUGINS = plugins


# ===========================================================================
#  Stage 1 — Should We Compact?
#
#  Have we accumulated enough raw text that it's worth spending an 
#  API call to compress it? We estimate the total token count across
#  all segments and compare against a configurable threshold.
# ===========================================================================

def _default_should_compact(state, segments, config):
    token_threshold = config.get("compactionTokenThreshold", 50000)
    total_tokens = sum(estimate_tokens(s.get("content", "")) for s in segments)

    if total_tokens >= token_threshold:
        return True, f"token_threshold ({total_tokens} >= {token_threshold})"

    return False, None


# ===========================================================================
#  Stage 2 — Which Segments Get Compacted?
#
#  We always compact from the oldest end — these are the most "stale"
#  memories. We walk forward through segments, accumulating tokens until
#  we hit the budget. Two safety rails:
#
#    1. Never compact more than 20% of the threshold in one pass.
#       This keeps each compaction small and focused.
#
#    2. Never compact ALL segments — at least one must survive so
#       Claude always has some raw context to work with.
# ===========================================================================

def _default_select_segments(segments, config):
    tokens_to_compact = config.get("tokensToCompact", 10000)
    token_threshold = config.get("compactionTokenThreshold", 50000)

    # Cap at 20% of the threshold to keep compactions bite-sized
    max_compact = int(token_threshold * 0.2)
    if tokens_to_compact > max_compact:
        tokens_to_compact = max_compact

    # Walk oldest-first, accumulating tokens until we hit the budget
    accumulated = 0
    count = 0
    for seg in segments:
        content = (
            seg.get("content", "") if isinstance(seg, dict)
            else getattr(seg, "content", "")
        )
        seg_tokens = estimate_tokens(content)

        # Stop if adding this segment would exceed budget (but always take at least one)
        if accumulated + seg_tokens > tokens_to_compact and count > 0:
            break

        accumulated += seg_tokens
        count += 1

    count = max(1, count)                    # Always compact at least 1 segment
    count = min(count, len(segments) - 1)    # Never compact everything

    return segments[:count], segments[count:]


# ===========================================================================
#  Stage 3 — Format Segments for the Extraction LLM
#
#  The extraction model needs structured input to produce good insights.
#  Each segment gets a header with its cycle number, timestamp, and a
#  summary of which tools were called. This metadata helps the LLM
#  understand the *context* of each thought, not just the text.
# ===========================================================================

def _default_format_segments(to_compact, state):
    cycle_nums = [s.get("cycle", "?") for s in to_compact]
    cycle_range = f"Cycles {cycle_nums[0]}-{cycle_nums[-1]}"

    parts = [f"[Compacting {cycle_range} — {len(to_compact)} segments]", ""]

    for seg in to_compact:
        cycle = seg.get("cycle", "?")
        content = seg.get("content", "")
        timestamp = seg.get("timestamp", "")

        header = f"[Cycle {cycle}]"
        if timestamp:
            header += f" ({timestamp})"

        # Include tool names so the extraction LLM knows what actions were taken
        tool_actions = seg.get("toolActions", [])
        if tool_actions:
            tool_names = [
                a.get("tool", "unknown")
                for a in tool_actions
                if isinstance(a, dict)
            ]
            if tool_names:
                header += f" [tools: {', '.join(tool_names)}]"

        parts.extend([header, content, "", "---", ""])

    return "\n".join(parts)


# ===========================================================================
#  Stage 4 — Post-Process the Extraction Output
#
#  The raw LLM output gets wrapped with a header identifying which
#  compaction produced it and which cycles it covers. If the LLM decided
#  nothing was worth extracting, we return empty string to skip appending.
# ===========================================================================

def _default_post_process(raw_insights, compaction_num, compacted_cycles, state):
    if not raw_insights or raw_insights.strip() == "(no new insights)":
        return ""

    header = (
        f"### Compaction #{compaction_num} "
        f"(Cycles {compacted_cycles[0]}-{compacted_cycles[-1]})"
    )
    return f"{header}\n\n{raw_insights.strip()}"


# ===========================================================================
#  Main Compaction Engine
#
#  This is the orchestrator that wires the four stages together. Each stage
#  tries the plugin version first, catches any crash, and falls back to
#  the built-in default. The two-layer architecture works like this:
#
#    Layer 1 (every compaction):
#      Raw segments → LLM extraction → tagged insights appended to
#      the Insights buffer.
#
#    Layer 2 (only when insights overflow):
#      Oldest insights get "staged" and sent to a second LLM call that
#      decides which ones deserve permanent integration into the Core
#      Identity document. The rest are archived and dropped.
#
#  This creates a natural memory hierarchy: recent thoughts live as raw
#  segments, mature thoughts become insights, and the most important
#  insights become part of the AI's persistent self-concept.
# ===========================================================================

def run_compaction(state, extraction_prompt_base=None, integration_prompt_base=None,
                   archive_fn=None, log_dropped_fn=None):
    """
    Run the full two-layer compaction pipeline.

    Args:
        state:  The engine state dict containing segments, insights, memoryCore, config, etc.
        extraction_prompt_base:  Base prompt for the Layer 1 extraction LLM call.
        integration_prompt_base: Base prompt for the Layer 2 core integration LLM call.
        archive_fn:              Optional callback to archive the core before Layer 2 modifies it.
        log_dropped_fn:          Optional callback to log insights being dropped from the buffer.

    Returns:
        True if compaction ran, False if skipped.
    """
    extraction_prompt_base = extraction_prompt_base or EXTRACTION_PROMPT_FALLBACK
    integration_prompt_base = integration_prompt_base or INTEGRATION_PROMPT_FALLBACK

    config = state.get("config", {})
    segments = state.get("segments", [])

    # ── Stage 1: Should we compact? ──────────────────────────────────
    # Try the plugin first; if it crashes, fall back to default.
    should_compact_fn = COMPACTION_PLUGINS.get("should_compact", _default_should_compact)
    try:
        should, trigger_reason = should_compact_fn(state, segments, config)
    except Exception as e:
        logging.error(f"Plugin should_compact crashed: {e}, using default")
        should, trigger_reason = _default_should_compact(state, segments, config)

    if not should:
        return False

    logging.info(f"Compaction triggered: {trigger_reason}")

    # ── Stage 2: Select which segments to compact ────────────────────
    select_fn = COMPACTION_PLUGINS.get("select_segments", _default_select_segments)
    try:
        to_compact, remaining = select_fn(segments, config)
    except Exception as e:
        logging.error(f"Plugin select_segments crashed: {e}, using default")
        to_compact, remaining = _default_select_segments(segments, config)

    # Hard safety invariant: never compact everything, no matter what the plugin did
    if not remaining:
        remaining = [to_compact[-1]]
        to_compact = to_compact[:-1]
    if not to_compact:
        return False

    compaction_num = state.get("totalCompactions", 0) + 1
    compacted_cycles = [s.get("cycle", "?") for s in to_compact]

    # ── Stage 3: Format segments for the extraction LLM ──────────────
    current_core = state.get("memoryCore", "")
    current_insights = state.get("insights", "")

    format_fn = COMPACTION_PLUGINS.get("format_segments", _default_format_segments)
    try:
        thoughts_text = format_fn(to_compact, state)
    except Exception as e:
        logging.error(f"Plugin format_segments crashed: {e}, using default")
        thoughts_text = _default_format_segments(to_compact, state)

    # Build the full extraction prompt and call the LLM
    extraction_prompt = (
        f"{extraction_prompt_base}\n\n---\n\n"
        f"## CURRENT CORE IDENTITY:\n{current_core or '(not yet established)'}\n\n"
        f"## CURRENT INSIGHTS LAYER:\n{current_insights or '(empty)'}\n\n"
        f"## SEGMENTS BEING COMPACTED:\n{thoughts_text}\n\n---\n\n"
        f"Extract significant insights from the segments above. "
        f"If nothing is genuinely significant, output exactly: (no new insights)"
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": extraction_prompt}],
    )
    new_insights = response.content[0].text.strip()

    # ── Stage 4: Post-process and append to insights buffer ──────────
    post_fn = COMPACTION_PLUGINS.get("post_process", _default_post_process)
    try:
        grouped_block = post_fn(new_insights, compaction_num, compacted_cycles, state)
    except Exception as e:
        logging.error(f"Plugin post_process crashed: {e}, using default")
        grouped_block = _default_post_process(new_insights, compaction_num, compacted_cycles, state)

    if grouped_block:
        if current_insights:
            state["insights"] = current_insights + "\n\n" + grouped_block
        else:
            state["insights"] = grouped_block

    # Discard the compacted segments and update bookkeeping
    state["segments"] = remaining
    state["totalCompactions"] = compaction_num
    state["lastCompactionCycle"] = state.get("totalCycles", 0)
    state["lastCompactedCycles"] = f"{compacted_cycles[0]}-{compacted_cycles[-1]}"

    # ══════════════════════════════════════════════════════════════════
    #  Layer 2: Insights Overflow → Core Identity Integration
    #
    #  The insights buffer has a max size. When it overflows, the oldest
    #  ~30% gets "staged" — sent to a second LLM that decides what to
    #  weave into Core Identity and what to drop. We split on paragraph
    #  boundaries so we don't cut an insight in half.
    #
    #  This is the mechanism that turns short-term observations into
    #  long-term self-knowledge. It only fires when needed, so most
    #  compaction cycles are just Layer 1.
    # ══════════════════════════════════════════════════════════════════

    insights_text = state.get("insights", "")
    max_insights = config.get("insightsMaxChars", 1000)
    overflow_pct = config.get("insightsOverflowPercent", 0.3)

    if len(insights_text) > max_insights:

        # Archive the current core before we let the LLM modify it
        if current_core and archive_fn:
            try:
                archive_fn(
                    current_core, compaction_num,
                    compacted_cycles=f"{compacted_cycles[0]}-{compacted_cycles[-1]}"
                )
            except Exception as e:
                logging.warning(f"Failed to archive core before integration: {e}")

        # Split insights at ~30%, rounding to the nearest paragraph boundary
        split_point = int(len(insights_text) * overflow_pct)
        newline_pos = insights_text.find("\n\n", split_point)
        if newline_pos != -1:
            split_point = newline_pos + 2
        else:
            split_point = len(insights_text)

        staged_insights = insights_text[:split_point]
        remaining_insights = insights_text[split_point:].lstrip()

        # Log what we're about to drop from the buffer (for auditability)
        if log_dropped_fn:
            try:
                log_dropped_fn(staged_insights, compaction_num, state.get("totalCycles", 0))
            except Exception as e:
                logging.warning(f"Failed to log dropped insights: {e}")

        # Ask the LLM to integrate the staged insights into Core Identity
        core_max_chars = config.get("coreMaxChars", 3500)
        integration_prompt = (
            f"{integration_prompt_base}\n\n---\n\n"
            f"## CURRENT CORE IDENTITY:\n{current_core or '(not yet established)'}\n\n"
            f"## STAGED INSIGHTS FOR EVALUATION:\n{staged_insights}\n\n---\n\n"
            f"Return the updated Core Identity. Target ~{core_max_chars} characters."
        )

        integration_response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": integration_prompt}],
        )

        # The LLM returns the complete new Core Identity document
        state["memoryCore"] = integration_response.content[0].text.strip()
        state["insights"] = remaining_insights

    return True
