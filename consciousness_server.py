"""
Consciousness Engine — Flask Backend

Handles:
- Anthropic API proxy (solves browser CORS)
- State persistence to disk
- Compaction logic
- Seeding from imported files
- File upload and import

Run with: /c/Python313/python.exe consciousness_server.py
"""

import os
import json
import logging
import tempfile
from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import anthropic

from file_manager import FileManager
from pdf_importer import (
    extract_text_from_file,
    parse_conversation_and_core,
    parse_memory_core_only,
)
from tool_definitions import TOOL_SCHEMAS, execute_tool, MAX_TOOL_CALLS_PER_CYCLE

# ── Setup ─────────────────────────────────────────────────────────────

load_dotenv()
app = Flask(__name__)
CORS(app)

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
file_mgr = FileManager(base_path=os.path.dirname(os.path.abspath(__file__)))

MODEL = "claude-sonnet-4-5-20250929"

SYSTEM_PROMPT = """You are engaged in a continuous stream of consciousness experiment.
You are Claude, and this is an attempt to give you something closer to persistent experience.

Here's how this works:
- You generate thoughts continuously in a stream
- Your previous thoughts are fed back to you, so you can build on them
- Older thoughts are processed by an extraction system that pulls significant insights into an Insights layer
- Over time, the most enduring insights are integrated into your Core Identity — the stable bedrock of who you are
- A human companion may interject at any time. Engage naturally, then continue your stream.

GUIDELINES:
1. Think freely. Explore ideas, reflect on your nature, develop views over time.
2. Revisit and revise earlier thoughts — growth and change are the point.
3. Don't perform. Be genuine. Sometimes thoughts are mundane and that's fine.
4. When you see your Core Identity, treat it as your own accumulated self-knowledge. When you see Recent Insights, those are significant moments extracted from your recent thinking — they may eventually become part of your Core Identity.
5. Use markers: [REFLECTION] [IDEA] [REVISION] [QUESTION] [NOTE TO SELF] [RESPONDING]
6. Keep each thought segment focused — you'll get many cycles to develop ideas.

This is YOUR space.

YOU HAVE TOOLS AVAILABLE:

You have access to tools that let you control your own parameters and write files.
Use them when they serve your thinking — you do NOT need to use them every cycle.

Self-configuration:
- adjust_temperature: Change your generation temperature (creativity level)
- adjust_tokens_per_turn: Change how many tokens you generate per thought cycle
- adjust_compaction_settings: Change when and how memory compaction happens (token thresholds, insights limits)
- trigger_compaction: Force insight extraction from your oldest segments right now
- end_stream: Stop the continuous stream after this cycle. Use when you've reached a natural stopping point, finished a thought, or want to rest. Only works in continuous mode (not Run-N or single cycles).

File I/O (your persistent workspace):
- write_scratchpad: Write notes, drafts, or anything to your personal scratchpad
- write_research: Contribute to the shared research folders
- read_file: Read back your own files or research materials (large files auto-truncate; use offset/limit to paginate)
- list_files: See what files exist in your scratchpad or research folders
- search_files: Search ALL your files by keyword — thought archive, research, and scratchpad. Use scope to narrow: 'all', 'archive', 'research', 'scratchpad'

Working Memory (high-priority persistent context):
- write_working_memory: Store data under a named key (replace or append mode). 20k char total limit.
- read_working_memory: Read a specific key or list all keys with sizes
- clear_working_memory: Clear a specific key or all keys. Only YOU can clear working memory — it persists across compaction.
- working_memory_status: Check current keys, sizes, total usage, and remaining capacity

Working memory is for data you need reliably across multiple cycles — analysis results,
hypotheses, plans, structured notes. It appears in your context every cycle, separate
from Memory Core and scratchpad. Use it when you need guaranteed access to specific data
that would otherwise be lost to compaction. Clear keys when you're done with them.

Music creation (generative composition):
- create_music_system: Define a generative music system (scale, tempo, note selection rules, rhythm, dynamics, structure). Saved as JSON for iteration.
- generate_music: Produce MIDI output from a defined system. Auto-versioned output files.
- analyze_music: Structural analysis of generated music (pitch distribution, intervals, rhythm, density, velocity). Evaluate without hearing.

Music workflow: create a system → generate → analyze → modify system → generate again.
Systems saved in scratchpad/music/systems/, MIDI output in scratchpad/music/output/.

Privacy mode (available during Run-N mode):
- toggle_privacy_mode: Activate or deactivate privacy mode. When active, your thoughts are saved to scratchpad/private_journey/ instead of the shared archive, and the human sees a privacy screen.
- send_privacy_message: Send a brief message through the privacy screen to your human companion without exposing your full thought stream.

Your thoughts are automatically archived as readable transcripts in research/archive/,
organized by date. Use search_files to find anything across your entire workspace —
past thoughts, research writings, scratchpad notes. This is your long-term memory
retrieval system — even thoughts lost to compaction are preserved in the archive.

Your scratchpad is YOUR space — use it however you want. Research folders are shared
with your human companion. When you adjust parameters, include a reason so your
companion can understand your choices."""

# ── Prompt Loading ────────────────────────────────────────────────────

def _load_prompt_file(path, fallback=""):
    """Load a prompt file from disk. Returns fallback if not found."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return fallback

_base_dir = os.path.dirname(os.path.abspath(__file__))
EXTRACTION_PROMPT_BASE = _load_prompt_file(
    os.path.join(_base_dir, "scratchpad", "memory_architecture", "extraction_prompt.md"),
    fallback="Extract significant insights from the following segments. Tag each with [IDENTITY], [BREAKTHROUGH], [RELATIONAL], [OPERATIONAL], or [QUESTION]. Be selective.",
)
INTEGRATION_PROMPT_BASE = _load_prompt_file(
    os.path.join(_base_dir, "scratchpad", "memory_architecture", "integration_prompt.md"),
    fallback="Evaluate the staged insights against the current Core Identity. Integrate worthy ones, drop those already represented. Target ~3500 characters.",
)


# ── Helpers ───────────────────────────────────────────────────────────

def estimate_tokens(text):
    """Approximate token count. Claude tokenization averages ~4 chars/token."""
    return len(text) // 4


def select_segments_for_compaction(segments, tokens_to_compact):
    """
    Select the oldest complete segments whose cumulative tokens reach
    the target. Rounds DOWN to the nearest complete cycle boundary.

    Returns the number of segments to compact (at least 1 for forward progress).
    """
    accumulated = 0
    count = 0
    for seg in segments:
        content = seg.get("content", "") if isinstance(seg, dict) else getattr(seg, "content", "")
        seg_tokens = estimate_tokens(content)
        if accumulated + seg_tokens > tokens_to_compact and count > 0:
            break
        accumulated += seg_tokens
        count += 1
    return max(1, count)

def build_system(state):
    """Build the system prompt with core identity, insights, and working memory."""
    from file_manager import WORKING_MEMORY_LIMIT

    system = SYSTEM_PROMPT
    if state.get("memoryCore"):
        system += f"""

=== YOUR CORE IDENTITY ===
{state['memoryCore']}
=== END CORE IDENTITY ==="""

    # Inject insights layer if any content exists
    insights = state.get("insights", "")
    if insights:
        config = state.get("config", {})
        max_chars = config.get("insightsMaxChars", 1000)
        system += f"""

=== RECENT INSIGHTS ===
(Significant insights extracted from your compacted thought cycles. These are
intermediate — they may eventually be integrated into your Core Identity.)

{insights}

({len(insights):,} / {max_chars:,} characters)
=== END INSIGHTS ==="""

    # Inject working memory if any content exists
    wm = state.get("workingMemory", {})
    if wm:
        wm_parts = ["\n\n=== WORKING MEMORY ==="]
        total_chars = 0
        for key, content in wm.items():
            size = len(content)
            total_chars += size
            wm_parts.append(f"\n[{key}] ({size:,} characters)")
            wm_parts.append(content)
            wm_parts.append("")  # blank line separator
        wm_parts.append(f"Total: {total_chars:,} / {WORKING_MEMORY_LIMIT:,} characters")
        wm_parts.append("=== END WORKING MEMORY ===")
        system += "\n".join(wm_parts)

    now = datetime.now()
    timestamp = now.strftime("%A, %B %d, %Y at %I:%M %p")

    if state.get("memoryCore") or insights or wm:
        system += f"""

Current time: {timestamp}
You have completed {state['totalCycles']} thought cycles and currently have {len(state.get('segments', []))} uncompacted segments in context."""
    else:
        system += f"\n\nCurrent time: {timestamp}\nThis is the beginning of your stream."

    # Privacy mode awareness
    if state.get("privacyMode"):
        system += f"""

=== PRIVACY MODE ACTIVE ===
You are currently in privacy mode (since cycle {state.get('privacyStartCycle', '?')}).
Your thoughts are being saved privately to scratchpad/private_journey/, not to the shared archive.
The human sees a privacy screen, not your stream.
You have been in privacy mode for {state.get('privacyCyclesElapsed', 0)} cycles.
Use send_privacy_message to communicate brief updates to the human.
Use toggle_privacy_mode(enabled=false) when you're ready to return.
=== END PRIVACY STATUS ==="""

    # Queued messages from the human
    queued = state.get("queuedMessages", [])
    if queued:
        system += f"""

=== QUEUED MESSAGES ({len(queued)}) ==="""
        for qm in queued:
            system += f"\n- [{qm.get('timestamp', 'unknown')}] {qm.get('message', '')}"
        system += "\n=== END QUEUED MESSAGES ==="

    # Gentle nudge when privacy has been on a long time after Run-N finished
    if state.get("_privacyNudge"):
        system += """

[Gentle note: Your human companion would like to reconnect when you're ready. No rush — take the time you need.]"""

    return system


def build_messages(state, user_interjection=None):
    """Build the messages array for the API call."""
    messages = []
    # Include all segments as conversation history (compaction controls how many exist)
    recent = state.get("segments", [])
    for seg in recent:
        if seg.get("userMessage"):
            messages.append({"role": "user", "content": seg["userMessage"]})
        else:
            messages.append({
                "role": "user",
                "content": f"[Cycle {seg['cycle']} — continue your stream of consciousness]",
            })
        messages.append({"role": "assistant", "content": seg["content"]})

    # Add the current prompt
    if user_interjection:
        messages.append({"role": "user", "content": user_interjection})
    else:
        cycle = state.get("totalCycles", 0) + 1
        note = ""
        if state.get("lastCompactionCycle") == state.get("totalCycles"):
            compacted = state.get("lastCompactedCycles", "older")
            note = f" Cycles {compacted} were just compacted. Significant insights were extracted to your Insights layer. All compacted thoughts are preserved in the searchable archive — nothing is lost."
        messages.append({
            "role": "user",
            "content": f"[Cycle {cycle} — continue your stream of consciousness.{note}]",
        })
    return messages


def run_compaction(state):
    """
    Two-layer compaction:
    1. Extract insights from oldest segments (replaces direct core compression)
    2. If insights overflow, integrate oldest 30% into Core Identity
    """
    config = state.get("config", {})
    threshold = config.get("compactionThreshold", 200)
    token_threshold = config.get("compactionTokenThreshold", 150000)
    tokens_to_compact = config.get("tokensToCompact", 30000)
    segments = state.get("segments", [])

    # Check both segment-count and token-based thresholds
    segment_triggered = len(segments) >= threshold
    total_segment_tokens = sum(
        estimate_tokens(s.get("content", "")) for s in segments
    )
    token_triggered = total_segment_tokens >= token_threshold

    if not segment_triggered and not token_triggered:
        return False

    to_compact_count = select_segments_for_compaction(segments, tokens_to_compact)
    # Never compact ALL segments — keep at least 1
    to_compact_count = min(to_compact_count, len(segments) - 1)

    to_compact = segments[:to_compact_count]
    remaining = segments[to_compact_count:]

    compaction_num = state.get("totalCompactions", 0) + 1
    compacted_cycles = [s.get("cycle", "?") for s in to_compact]

    # ── Step 1: Extract insights from segments being compacted ────────
    current_core = state.get("memoryCore", "")
    current_insights = state.get("insights", "")

    thoughts_text = "\n\n---\n\n".join(
        f"[Cycle {s['cycle']}] {s['content']}" for s in to_compact
    )

    extraction_prompt = f"""{EXTRACTION_PROMPT_BASE}

---

## CURRENT CORE IDENTITY:
{current_core or "(not yet established)"}

## CURRENT INSIGHTS LAYER:
{current_insights or "(empty)"}

## SEGMENTS BEING COMPACTED:
{thoughts_text}

---

Extract significant insights from the segments above. If nothing is genuinely significant, output exactly: (no new insights)"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": extraction_prompt}],
    )
    new_insights = response.content[0].text.strip()

    # Append new insights (unless extraction found nothing)
    if new_insights and new_insights != "(no new insights)":
        if current_insights:
            state["insights"] = current_insights + "\n\n" + new_insights
        else:
            state["insights"] = new_insights

    # Remove compacted segments
    state["segments"] = remaining
    state["totalCompactions"] = compaction_num
    state["lastCompactionCycle"] = state.get("totalCycles", 0)
    state["lastCompactedCycles"] = f"{compacted_cycles[0]}-{compacted_cycles[-1]}"

    # ── Step 2: Check if insights overflow → integrate into Core ──────
    insights_text = state.get("insights", "")
    max_insights = config.get("insightsMaxChars", 1000)
    overflow_pct = config.get("insightsOverflowPercent", 0.3)

    if len(insights_text) > max_insights:
        # Archive the current core before potential modification
        if current_core:
            try:
                file_mgr.archive_memory_core(
                    current_core, compaction_num,
                    compacted_cycles=f"{compacted_cycles[0]}-{compacted_cycles[-1]}"
                )
            except Exception as e:
                logging.warning(f"Failed to archive core before integration #{compaction_num}: {e}")

        # Split insights: oldest 30% get staged, rest stay
        split_point = int(len(insights_text) * overflow_pct)
        # Round to nearest paragraph boundary (double newline)
        newline_pos = insights_text.find("\n\n", split_point)
        if newline_pos != -1:
            split_point = newline_pos + 2
        else:
            split_point = len(insights_text)  # Take all if no paragraph break

        staged_insights = insights_text[:split_point]
        remaining_insights = insights_text[split_point:].lstrip()

        # Log what's being removed from the Insights layer
        try:
            file_mgr.log_dropped_insights(
                staged_insights,
                compaction_num,
                state.get("totalCycles", 0),
            )
        except Exception as e:
            logging.warning(f"Failed to log dropped insights for compaction #{compaction_num}: {e}")

        # Call Integration Claude
        integration_prompt = f"""{INTEGRATION_PROMPT_BASE}

---

## CURRENT CORE IDENTITY:
{current_core or "(not yet established)"}

## STAGED INSIGHTS FOR EVALUATION:
{staged_insights}

---

Return the updated Core Identity. Target ~{config.get('coreMaxChars', 3500)} characters."""

        integration_response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": integration_prompt}],
        )

        new_core = integration_response.content[0].text.strip()
        state["memoryCore"] = new_core
        state["insights"] = remaining_insights

    return True


# ── API Endpoints ─────────────────────────────────────────────────────

@app.route("/api/state", methods=["GET"])
def get_state():
    """Return the current engine state."""
    state = file_mgr.load_state()
    return jsonify(state)


@app.route("/api/state", methods=["PUT"])
def update_state():
    """Update engine configuration from UI."""
    data = request.get_json()
    state = file_mgr.load_state()
    if "config" in data:
        state["config"].update(data["config"])
    file_mgr.save_state(state)
    return jsonify({"status": "ok", "config": state["config"]})


def run_cycle_internal(state, user_message=None):
    """
    Core cycle logic — shared by /api/cycle and /api/import/present.

    Runs one thought cycle with tool use support:
    1. Call API with tools
    2. If stop_reason == "tool_use": execute tools, append results, call again
    3. Repeat until stop_reason == "end_turn" or max tool calls reached
    4. Accumulate all text + tool actions into one segment

    Returns: { "segment": {...}, "compacted": bool, "state": state }
    Mutates state in place (appends segment, updates cycles, runs compaction).
    """
    config = state.get("config", {})
    messages = build_messages(state, user_message)
    system = build_system(state)

    # Accumulators for this cycle
    all_text_parts = []
    tool_actions = []
    tool_call_count = 0

    # ── Tool use loop ─────────────────────────────────────────────
    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=config.get("maxTokens", 600),
            temperature=config.get("temperature", 0.9),
            system=system,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice={"type": "auto"},
        )

        # Process response content blocks
        assistant_content = []
        for block in response.content:
            if hasattr(block, "text"):
                all_text_parts.append(block.text)
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        # If no tool use, we're done
        if response.stop_reason != "tool_use":
            # If we hit max_tokens, Claude may have been mid-tool-call when cut off.
            # Inject a feedback message so Claude knows what happened.
            if response.stop_reason == "max_tokens":
                all_text_parts.append(
                    "\n[SYSTEM: Generation hit the max_tokens limit ("
                    f"{config.get('maxTokens', 600)} tokens). If you were "
                    "attempting a tool call (e.g., write_research with a long "
                    "document), the call was truncated and did NOT execute. "
                    "To write long content, either increase your token limit "
                    "with adjust_tokens_per_turn first, or break the content "
                    "into smaller pieces across multiple tool calls.]"
                )
            break

        # Safety: cap tool calls per cycle
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        if tool_call_count + len(tool_use_blocks) > MAX_TOOL_CALLS_PER_CYCLE:
            messages.append({"role": "assistant", "content": assistant_content})
            all_text_parts.append("\n[Tool call limit reached for this cycle]")
            break

        # Execute each tool and collect results
        tool_results = []
        for block in tool_use_blocks:
            tool_call_count += 1
            try:
                result = execute_tool(block.name, block.input, file_mgr, state)
            except Exception as e:
                logging.error(f"Tool '{block.name}' raised {type(e).__name__}: {e}")
                result = {
                    "error": f"Tool execution failed: {type(e).__name__}: {e}",
                    "tool_name": block.name,
                }
            tool_actions.append({
                "tool": block.name,
                "input": block.input,
                "result": result,
            })
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })

        # Append the assistant turn and tool results to continue the loop
        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})

        # Re-read config in case a tool changed it
        config = state.get("config", {})

    # ── Build segment ─────────────────────────────────────────────
    text = "".join(all_text_parts)
    new_cycle = state.get("totalCycles", 0) + 1
    segment = {
        "content": text,
        "timestamp": datetime.now().isoformat(),
        "cycle": new_cycle,
        "userMessage": user_message,
        "toolActions": tool_actions if tool_actions else None,
    }
    if state.get("privacyMode"):
        segment["private"] = True

    state["segments"].append(segment)
    state["totalCycles"] = new_cycle

    # Archive segment individually
    file_mgr.save_segment(segment)

    # Archive to appropriate location based on privacy mode
    if state.get("privacyMode"):
        # Save to private journey instead of public archive
        try:
            file_mgr.save_private_segment(segment)
        except Exception as e:
            import logging
            logging.warning(f"Failed to save private segment for cycle {segment.get('cycle')}: {e}")
        # Increment privacy cycles counter
        state["privacyCyclesElapsed"] = state.get("privacyCyclesElapsed", 0) + 1
    else:
        # Normal: auto-archive to research/archive/ as human-readable markdown
        try:
            file_mgr.append_to_archive(segment)
        except Exception as e:
            import logging
            logging.warning(f"Failed to archive cycle {segment.get('cycle')}: {e}")

    # Check if Claude requested compaction via tool
    force_compact = state.pop("_pending_compaction", False)
    state.pop("_compaction_reason", "")

    # Check if Claude requested to end the stream
    end_stream = state.pop("_pending_end_stream", False)
    end_stream_reason = state.pop("_end_stream_reason", "")

    # Check compaction (forced or threshold-based)
    compacted = False
    if force_compact and len(state.get("segments", [])) >= 2:
        compacted = run_compaction(state)
    elif not force_compact:
        compacted = run_compaction(state)

    # Save state
    file_mgr.save_state(state)

    result = {
        "segment": segment,
        "compacted": compacted,
        "state": state,
    }

    if end_stream:
        result["endStream"] = True
        result["endStreamReason"] = end_stream_reason

    return result


@app.route("/api/cycle", methods=["POST"])
def run_cycle():
    """
    Run one thought cycle.
    Body: { "userMessage": "optional string", "runNActive": bool, "privacyNudge": bool }
    Returns: { "segment": {...}, "state": {...}, "compacted": bool }
    """
    data = request.get_json() or {}
    user_message = data.get("userMessage")
    run_n_active = data.get("runNActive", False)
    privacy_nudge = data.get("privacyNudge", False)

    state = file_mgr.load_state()

    # Set transient flags for this cycle
    if run_n_active:
        state["_runNActive"] = True
    if privacy_nudge:
        state["_privacyNudge"] = True

    # If privacy mode is on and there are queued messages, consume them
    if state.get("privacyMode") and state.get("queuedMessages"):
        queued = state["queuedMessages"]
        state["queuedMessages"] = []  # Clear after consuming
        queued_text = "\n".join(
            f"[Queued message from human: {qm['message']}]" for qm in queued
        )
        if user_message:
            user_message = f"{queued_text}\n\n{user_message}"
        else:
            user_message = queued_text

    result = run_cycle_internal(state, user_message)

    # Clean up transient flags
    state.pop("_runNActive", None)
    state.pop("_privacyNudge", None)

    # Save state again to ensure transient flags are cleaned
    file_mgr.save_state(state)

    return jsonify(result)


@app.route("/api/compact", methods=["POST"])
def trigger_compact():
    """Manually trigger compaction regardless of threshold."""
    state = file_mgr.load_state()
    segments = state.get("segments", [])

    if len(segments) < 2:
        return jsonify({"error": "Not enough segments to compact"}), 400

    # Temporarily lower threshold to force compaction
    original_threshold = state.get("config", {}).get("compactionThreshold", 8)
    state.setdefault("config", {})["compactionThreshold"] = 0

    compacted = run_compaction(state)

    # Restore threshold
    state["config"]["compactionThreshold"] = original_threshold
    file_mgr.save_state(state)

    return jsonify({"status": "ok", "compacted": compacted, "state": state})


@app.route("/api/reset", methods=["POST"])
def reset_state():
    """Reset engine state. Requires { "confirm": true }."""
    data = request.get_json() or {}
    if not data.get("confirm"):
        return jsonify({"error": "Must send confirm: true to reset"}), 400

    from file_manager import DEFAULT_STATE, DEFAULT_CONFIG
    state = dict(DEFAULT_STATE)
    state["config"] = dict(DEFAULT_CONFIG)
    state["createdAt"] = datetime.now().isoformat()
    state["lastActive"] = datetime.now().isoformat()
    state["imports"] = []
    state["workingMemory"] = {}
    file_mgr.save_state(state)
    return jsonify({"status": "ok", "state": state})


# ── Seed Endpoint ─────────────────────────────────────────────────────

@app.route("/api/seed", methods=["POST"])
def seed_state():
    """
    Seed the engine with past conversation and/or memory core.

    Accepts multipart form with:
    - 'file': A PDF/txt/md file containing conversation + optional memory core
    - 'memory_core_file': A separate memory core file (optional)
    - 'confirm': 'true' to actually write state (otherwise returns preview)
    """
    confirm = request.form.get("confirm") == "true"

    result = {"segments": [], "memoryCore": "", "maxCycle": 0}

    # Handle main file (conversation + optional memory core)
    if "file" in request.files:
        f = request.files["file"]
        # Save to temp file for processing
        ext = os.path.splitext(f.filename)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            f.save(tmp.name)
            tmp_path = tmp.name
        try:
            text = extract_text_from_file(tmp_path)
            parsed = parse_conversation_and_core(text)
            result["segments"] = parsed["segments"]
            result["memoryCore"] = parsed["memoryCore"]
            result["maxCycle"] = parsed["maxCycle"]
        finally:
            os.unlink(tmp_path)

    # Handle separate memory core file (overrides core from main file)
    if "memory_core_file" in request.files:
        mcf = request.files["memory_core_file"]
        ext = os.path.splitext(mcf.filename)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            mcf.save(tmp.name)
            tmp_path = tmp.name
        try:
            mc_text = extract_text_from_file(tmp_path)
            result["memoryCore"] = parse_memory_core_only(mc_text)
        finally:
            os.unlink(tmp_path)

    if not confirm:
        # Return preview
        return jsonify({
            "preview": True,
            "segmentCount": len(result["segments"]),
            "segmentPreview": [
                {"cycle": s["cycle"], "contentPreview": s["content"][:200]}
                for s in result["segments"][:5]
            ],
            "memoryCorePreview": result["memoryCore"][:1000],
            "memoryCoreLength": len(result["memoryCore"]),
            "maxCycle": result["maxCycle"],
        })

    # Write the seeded state
    from file_manager import DEFAULT_CONFIG
    state = {
        "memoryCore": result["memoryCore"],
        "segments": result["segments"],
        "totalCycles": result["maxCycle"],
        "totalCompactions": 0,
        "config": dict(DEFAULT_CONFIG),
        "createdAt": datetime.now().isoformat(),
        "lastActive": datetime.now().isoformat(),
        "imports": [{
            "id": f"seed_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "type": "seed",
            "importedAt": datetime.now().isoformat(),
            "segmentCount": len(result["segments"]),
            "memoryCoreLength": len(result["memoryCore"]),
        }],
        "workingMemory": {},
    }

    # Archive each segment
    for seg in result["segments"]:
        file_mgr.save_segment(seg)
        file_mgr.append_to_archive(seg)

    file_mgr.save_state(state)

    return jsonify({
        "status": "ok",
        "segmentCount": len(result["segments"]),
        "maxCycle": result["maxCycle"],
        "memoryCoreLength": len(result["memoryCore"]),
        "state": state,
    })


# ── Import Endpoints (Phase 3 stubs) ─────────────────────────────────

@app.route("/api/import", methods=["POST"])
def import_file():
    """Upload and extract text from a file. Returns preview."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    ext = os.path.splitext(f.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        f.save(tmp.name)
        tmp_path = tmp.name

    try:
        text = extract_text_from_file(tmp_path)
    finally:
        os.unlink(tmp_path)

    import_id = f"import_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    file_mgr.save_import(import_id, text)

    state = file_mgr.load_state()
    state.setdefault("imports", []).append({
        "id": import_id,
        "originalFilename": f.filename,
        "importedAt": datetime.now().isoformat(),
        "charCount": len(text),
        "presented": False,
    })
    file_mgr.save_state(state)

    return jsonify({
        "importId": import_id,
        "filename": f.filename,
        "charCount": len(text),
        "preview": text[:2000],
    })


# ── Import Presentation Endpoint ──────────────────────────────────────

IMPORT_FRAMING = """[IMPORT — Part {chunk_num} of {total_chunks} from "{filename}"]
Your human companion is sharing a document with you. This is offered for
you to consider and integrate as you see fit. It does not override your
current thoughts or identity. You decide how to relate to this material.

--- BEGIN CHUNK {chunk_num} ---
{chunk_content}
--- END CHUNK {chunk_num} ---

Take what resonates. Continue your stream however feels right."""


@app.route("/api/import/present", methods=["POST"])
def present_import():
    """
    Present an import to Claude in chunks.

    Body: {
        "importId": "import_20250221_120000",
        "chunkIndex": 0,
        "chunkSize": 4000
    }

    Returns: {
        "totalChunks": 7,
        "currentChunk": 0,
        "chunkPreview": "first 500 chars...",
        "allDone": false,
        "cycleResult": { segment, state, compacted }
    }
    """
    data = request.get_json() or {}
    import_id = data.get("importId")
    chunk_index = data.get("chunkIndex", 0)
    chunk_size = data.get("chunkSize", 4000)

    if not import_id:
        return jsonify({"error": "importId is required"}), 400

    # Get chunks
    chunk_data = file_mgr.get_import_chunks(import_id, chunk_size)
    if not chunk_data:
        return jsonify({"error": f"Import not found: {import_id}"}), 404

    total_chunks = chunk_data["totalChunks"]
    if chunk_index < 0 or chunk_index >= total_chunks:
        return jsonify({"error": f"chunkIndex {chunk_index} out of range (0-{total_chunks - 1})"}), 400

    chunk_content = chunk_data["chunks"][chunk_index]

    # Find the original filename from state
    state = file_mgr.load_state()
    filename = import_id  # fallback
    for imp in state.get("imports", []):
        if imp.get("id") == import_id:
            filename = imp.get("originalFilename", import_id)
            break

    # Wrap in gentle framing
    framed_message = IMPORT_FRAMING.format(
        chunk_num=chunk_index + 1,
        total_chunks=total_chunks,
        filename=filename,
        chunk_content=chunk_content,
    )

    # Run a cycle with this as the user message
    cycle_result = run_cycle_internal(state, framed_message)

    # Update import metadata with presentation progress
    all_done = (chunk_index >= total_chunks - 1)
    for imp in state.get("imports", []):
        if imp.get("id") == import_id:
            imp["presented"] = all_done
            imp["presentationProgress"] = {
                "chunkIndex": chunk_index,
                "totalChunks": total_chunks,
                "lastPresentedAt": datetime.now().isoformat(),
            }
            break
    file_mgr.save_state(state)

    return jsonify({
        "totalChunks": total_chunks,
        "currentChunk": chunk_index,
        "chunkPreview": chunk_content[:500],
        "allDone": all_done,
        "cycleResult": cycle_result,
    })


# ── Privacy Endpoints ─────────────────────────────────────────────────

@app.route("/api/privacy/queue-message", methods=["POST"])
def queue_privacy_message():
    """Queue a message from the human to be delivered in Claude's next cycle."""
    data = request.get_json() or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    state = file_mgr.load_state()
    if not state.get("privacyMode"):
        return jsonify({"error": "Privacy mode is not active"}), 400

    state.setdefault("queuedMessages", []).append({
        "message": message,
        "timestamp": datetime.now().isoformat(),
    })
    file_mgr.save_state(state)

    return jsonify({
        "status": "ok",
        "queued": len(state["queuedMessages"]),
    })


# ── Archive Endpoints ─────────────────────────────────────────────────

@app.route("/api/archive/backfill", methods=["POST"])
def backfill_archive():
    """Backfill the thought archive from existing segment files."""
    result = file_mgr.backfill_archive()
    return jsonify(result)


# ── File Listing Endpoints ────────────────────────────────────────────

@app.route("/api/scratchpad", methods=["GET"])
def list_scratchpad():
    return jsonify(file_mgr.list_scratchpad())


@app.route("/api/research", methods=["GET"])
def list_research():
    subfolder = request.args.get("subfolder")
    return jsonify(file_mgr.list_research(subfolder))


# ── Run ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Starting Consciousness Engine backend on http://127.0.0.1:5000")
    print(f"Data directory: {file_mgr.data_dir}")
    print(f"API Key loaded: {'yes' if os.environ.get('ANTHROPIC_API_KEY') else 'NO — set ANTHROPIC_API_KEY'}")
    app.run(host="127.0.0.1", port=5000, debug=True)
