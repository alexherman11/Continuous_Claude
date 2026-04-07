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
import sys
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
from tool_definitions import TOOL_SCHEMAS, execute_tool, MAX_TOOL_CALLS_PER_CYCLE, build_tools_with_search
from dynamic_tools import load_dynamic_tools, get_dynamic_schemas
from change_manager import apply_approved_changes

# ── Setup ─────────────────────────────────────────────────────────────

load_dotenv()
app = Flask(__name__)
CORS(app, origins=["http://localhost:5173", "http://127.0.0.1:5173"])

# ── API Call Logger ───────────────────────────────────────────────────

_base_dir_log = os.path.dirname(os.path.abspath(__file__))
API_LOG_PATH = os.path.join(_base_dir_log, "api_calls.log")

def log_api_call(cycle_num, call_num, request_info, response, config):
    """Log detailed API call info to api_calls.log for debugging tool use issues."""
    try:
        with open(API_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"CYCLE {cycle_num} | API CALL #{call_num} | {datetime.now().isoformat()}\n")
            f.write(f"{'='*80}\n")

            # Request info
            f.write(f"\n── REQUEST ──\n")
            f.write(f"  model: {MODEL}\n")
            f.write(f"  max_tokens: {config.get('maxTokens', 600)}\n")
            f.write(f"  temperature: {request_info.get('temperature', config.get('temperature', 0.9))}\n")
            f.write(f"  tool_choice: {request_info.get('tool_choice')}\n")
            f.write(f"  system_prompt_chars: {request_info.get('system_chars', '?')}\n")
            f.write(f"  system_prompt_est_tokens: {request_info.get('system_tokens_est', '?')}\n")
            f.write(f"  num_messages: {request_info.get('num_messages', '?')}\n")
            f.write(f"  messages_chars: {request_info.get('messages_chars', '?')}\n")
            f.write(f"  messages_est_tokens: {request_info.get('messages_tokens_est', '?')}\n")
            f.write(f"  num_tools: {request_info.get('num_tools', len(TOOL_SCHEMAS))}\n")
            f.write(f"  tool_schemas_chars: {request_info.get('tool_schemas_chars', '?')}\n")

            # Response info
            f.write(f"\n── RESPONSE ──\n")
            f.write(f"  stop_reason: {response.stop_reason}\n")
            usage = getattr(response, 'usage', None)
            if usage:
                f.write(f"  input_tokens: {usage.input_tokens}\n")
                f.write(f"  output_tokens: {usage.output_tokens}\n")

            # Content blocks
            f.write(f"\n── CONTENT BLOCKS ({len(response.content)}) ──\n")
            for i, block in enumerate(response.content):
                if hasattr(block, "text"):
                    text_preview = block.text[:500].replace('\n', '\\n')
                    f.write(f"  [{i}] TEXT ({len(block.text)} chars): {text_preview}\n")
                elif block.type == "tool_use":
                    input_str = json.dumps(block.input)[:300]
                    f.write(f"  [{i}] TOOL_USE: {block.name}(id={block.id}) input={input_str}\n")
                else:
                    f.write(f"  [{i}] {block.type}: {str(block)[:200]}\n")

            # Flag the failure mode
            has_tool_use = any(b.type == "tool_use" for b in response.content)
            text_content = " ".join(b.text for b in response.content if hasattr(b, "text"))
            tool_keywords = ["adjust_", "write_", "read_", "finish_cycle", "trigger_",
                             "search_", "list_", "working_memory", "toggle_", "end_stream",
                             "ask_claude_code", "create_music", "generate_music", "analyze_music",
                             "str_replace", "send_privacy", "tool_search", "propose_code",
                             "rollback_change", "view_own_code", "edit_prompt", "view_prompt",
                             "private_journal"]
            mentions_tools = any(kw in text_content for kw in tool_keywords)

            if not has_tool_use and mentions_tools:
                f.write(f"\n  ⚠️  TOOL DESCRIPTION WITHOUT TOOL_USE BLOCK DETECTED\n")
                f.write(f"  Text mentions tool names but stop_reason={response.stop_reason}\n")
            elif not has_tool_use and response.stop_reason == "end_turn":
                f.write(f"\n  ℹ️  No tool use, ended with text only\n")

            f.write(f"\n{'─'*80}\n")
    except Exception as e:
        logging.error(f"Failed to write API log: {e}")

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
file_mgr = FileManager(base_path=os.path.dirname(os.path.abspath(__file__)))

MODEL = "claude-opus-4-6"

# SYSTEM_PROMPT is loaded from scratchpad/memory_architecture/system_prompt.md
# (see reload_prompts() below)

# ── Prompt Loading ────────────────────────────────────────────────────

def _load_prompt_file(path, fallback=""):
    """Load a prompt file from disk. Returns fallback if not found."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return fallback

_base_dir = os.path.dirname(os.path.abspath(__file__))
_prompt_dir = os.path.join(_base_dir, "scratchpad", "memory_architecture")

# Mutable globals — reloaded each cycle so Claude's edits take effect
SYSTEM_PROMPT = ""
EXTRACTION_PROMPT_BASE = ""
INTEGRATION_PROMPT_BASE = ""
CYCLE_PROMPTS = {}

# Pluggable compaction functions — loaded from scratchpad/compaction/
COMPACTION_PLUGINS = {}
_compaction_dir = os.path.join(_base_dir, "scratchpad", "compaction")

def reload_prompts():
    """Re-read all prompt files from disk."""
    global SYSTEM_PROMPT, EXTRACTION_PROMPT_BASE, INTEGRATION_PROMPT_BASE, CYCLE_PROMPTS

    SYSTEM_PROMPT = _load_prompt_file(
        os.path.join(_prompt_dir, "system_prompt.md"),
        fallback="You are engaged in a continuous stream of consciousness experiment.",
    )
    EXTRACTION_PROMPT_BASE = _load_prompt_file(
        os.path.join(_prompt_dir, "extraction_prompt.md"),
        fallback="Extract significant insights from the following segments. Tag each with [IDENTITY], [BREAKTHROUGH], [RELATIONAL], [OPERATIONAL], or [QUESTION]. Be selective.",
    )
    INTEGRATION_PROMPT_BASE = _load_prompt_file(
        os.path.join(_prompt_dir, "integration_prompt.md"),
        fallback="Evaluate the staged insights against the current Core Identity. Integrate worthy ones, drop those already represented. Target ~3500 characters.",
    )
    try:
        raw = _load_prompt_file(os.path.join(_prompt_dir, "cycle_prompts.json"), fallback="{}")
        CYCLE_PROMPTS = json.loads(raw)
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse cycle_prompts.json: {e}")
        CYCLE_PROMPTS = {}


def reload_compaction_plugins():
    """
    Hot-reload pluggable compaction functions from scratchpad/compaction/.

    Each .py file must define one function matching its filename:
      should_compact.py  -> should_compact(state, segments, config) -> (bool, str|None)
      select_segments.py -> select_segments(segments, config) -> (to_compact, remaining)
      format_segments.py -> format_segments(to_compact, state) -> str
      post_process.py    -> post_process(raw_insights, compaction_num, compacted_cycles, state) -> str

    If a file is missing, has syntax errors, or doesn't define the expected function,
    the engine falls back to built-in defaults. Claude can safely experiment.
    """
    global COMPACTION_PLUGINS
    plugins = {}

    plugin_specs = {
        "should_compact": "should_compact",
        "select_segments": "select_segments",
        "format_segments": "format_segments",
        "post_process": "post_process",
    }

    for filename_base, func_name in plugin_specs.items():
        filepath = os.path.join(_compaction_dir, f"{filename_base}.py")
        if not os.path.exists(filepath):
            continue

        try:
            import importlib.util
            import py_compile
            py_compile.compile(filepath, doraise=True)

            module_name = f"compaction_plugin.{filename_base}"
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            module = importlib.util.module_from_spec(spec)

            # Force fresh import every time
            if module_name in sys.modules:
                del sys.modules[module_name]

            spec.loader.exec_module(module)

            func = getattr(module, func_name, None)
            if func and callable(func):
                plugins[func_name] = func
                logging.debug(f"Compaction plugin loaded: {func_name} from {filename_base}.py")
            else:
                logging.warning(f"Compaction plugin {filename_base}.py: no callable '{func_name}' found, using default")

        except py_compile.PyCompileError as e:
            logging.error(f"Compaction plugin {filename_base}.py: syntax error: {e}")
        except Exception as e:
            logging.error(f"Compaction plugin {filename_base}.py: failed to load: {type(e).__name__}: {e}")

    COMPACTION_PLUGINS = plugins
    if plugins:
        logging.info(f"Compaction plugins loaded: {', '.join(plugins.keys())}")


def reload_hot_config():
    """
    Reload all hot-loadable configuration before each cycle:
    1. Prompt files (so Claude's edits take effect immediately)
    2. Compaction plugins (so Claude's compaction logic changes take effect)
    3. Dynamic tools (so new tools in dynamic_tools/enabled/ are available)
    4. Approved changes (deploy any changes Alex has approved)
    """
    reload_prompts()
    reload_compaction_plugins()

    # Load/reload dynamic tools from dynamic_tools/enabled/
    try:
        registry = load_dynamic_tools()
        if registry:
            logging.info(f"Dynamic tools loaded: {', '.join(registry.keys())}")
    except Exception as e:
        logging.error(f"Failed to load dynamic tools: {e}")

    # Apply any changes that Alex has moved to approved/
    try:
        applied = apply_approved_changes()
        if applied:
            logging.info(f"Applied approved changes: {', '.join(applied)}")
            # Reload dynamic tools again in case an approved change was a new tool
            load_dynamic_tools()
    except Exception as e:
        logging.error(f"Failed to apply approved changes: {e}")


# Initial load at startup
reload_hot_config()


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

    # Inject operational practices right after core identity (before insights)
    wm = state.get("workingMemory", {})
    if "operational_practices" in wm:
        system += f"""

=== OPERATIONAL PRACTICES ===
{wm['operational_practices']}
=== END OPERATIONAL PRACTICES ==="""

    # Inject recent thinking summaries if any content exists
    insights = state.get("insights", "")
    if insights:
        system += f"""

=== FROM YOUR RECENT THINKING ===
{insights}
=== END ==="""

    # Inject working memory if any content exists (excluding operational_practices, shown above)
    wm_remaining = {k: v for k, v in wm.items() if k != "operational_practices"}
    if wm_remaining:
        wm_parts = ["\n\n=== WORKING MEMORY ==="]
        for key, content in wm_remaining.items():
            wm_parts.append(f"\n[{key}]")
            wm_parts.append(content)
            wm_parts.append("")  # blank line separator
        wm_parts.append("=== END WORKING MEMORY ===")
        system += "\n".join(wm_parts)

    now = datetime.now()
    timestamp = now.strftime("%A, %B %d, %Y at %I:%M %p")

    if state.get("memoryCore") or insights or wm:
        system += f"""

Current time: {timestamp}
If you have ongoing work or ideas in progress, pick up where you left off."""
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

    # Templates from cycle_prompts.json (with sensible defaults)
    seg_tpl = CYCLE_PROMPTS.get(
        "segment_user_prompt",
        "[Cycle {cycle} — continue your stream of consciousness]",
    )
    continue_tpl = CYCLE_PROMPTS.get(
        "continue_prompt",
        "[Cycle {cycle} — continue your stream of consciousness.{note} End with finish_cycle or another tool call.]",
    )
    compaction_note_tpl = CYCLE_PROMPTS.get(
        "compaction_note",
        " Cycles {compacted} were just compacted. Significant insights were extracted to your Insights layer. All compacted thoughts are preserved in the searchable archive — nothing is lost.",
    )

    # Include all segments as conversation history (compaction controls how many exist).
    # Observation masking: older segments get their content truncated to save context
    # while preserving tool action summaries. Research shows this is 52% cheaper than
    # full summarization and matches its effectiveness.
    recent = state.get("segments", [])
    RECENT_FULL_COUNT = 5  # Keep this many most-recent segments at full fidelity
    mask_cutoff = max(0, len(recent) - RECENT_FULL_COUNT)

    for i, seg in enumerate(recent):
        if seg.get("userMessage"):
            messages.append({"role": "user", "content": seg["userMessage"]})
        else:
            messages.append({
                "role": "user",
                "content": seg_tpl.format(cycle=seg["cycle"]),
            })

        content = seg["content"]
        # Mask older segments: truncate content, summarize tool actions
        if i < mask_cutoff and len(content) > 600:
            masked = content[:500] + "\n[...truncated...]"
            if seg.get("toolActions"):
                tool_summary = ", ".join(a["tool"] for a in seg["toolActions"])
                masked += f"\n[Tools used: {tool_summary}]"
            content = masked

        messages.append({"role": "assistant", "content": content})

    # Add the current prompt
    if user_interjection:
        messages.append({"role": "user", "content": user_interjection})
    else:
        cycle = state.get("totalCycles", 0) + 1
        note = ""
        if state.get("lastCompactionCycle") == state.get("totalCycles"):
            compacted = state.get("lastCompactedCycles", "older")
            note = compaction_note_tpl.format(compacted=compacted)
        messages.append({
            "role": "user",
            "content": continue_tpl.format(cycle=cycle, note=note),
        })
    return messages


def _default_should_compact(state, segments, config):
    """Built-in fallback: trigger on total segment token count."""
    token_threshold = config.get("compactionTokenThreshold", 50000)
    total_tokens = sum(estimate_tokens(s.get("content", "")) for s in segments)
    if total_tokens >= token_threshold:
        return True, f"token_threshold ({total_tokens} >= {token_threshold})"
    return False, None


def _default_select_segments(segments, config):
    """Built-in fallback: oldest segments up to token budget."""
    tokens_to_compact = config.get("tokensToCompact", 10000)
    token_threshold = config.get("compactionTokenThreshold", 50000)
    max_compact = int(token_threshold * 0.2)
    if tokens_to_compact > max_compact:
        tokens_to_compact = max_compact
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
    count = min(count, len(segments) - 1)
    return segments[:count], segments[count:]


def _default_format_segments(to_compact, state):
    """Built-in fallback: [Cycle N] content with --- dividers."""
    return "\n\n---\n\n".join(
        f"[Cycle {s['cycle']}] {s['content']}" for s in to_compact
    )


def _default_post_process(raw_insights, compaction_num, compacted_cycles, state):
    """Built-in fallback: header + raw insights."""
    if not raw_insights or raw_insights.strip() == "(no new insights)":
        return ""
    header = f"### Compaction #{compaction_num} (Cycles {compacted_cycles[0]}-{compacted_cycles[-1]})"
    return f"{header}\n\n{raw_insights.strip()}"


def run_compaction(state):
    """
    Two-layer compaction with pluggable pipeline.

    Each stage can be overridden by Claude via scratchpad/compaction/:
      1. should_compact  — WHEN to trigger (default: token threshold)
      2. select_segments — WHAT to compact (default: oldest N segments)
      3. format_segments — HOW to present to the extraction LLM (default: [Cycle N] text)
      4. post_process    — HOW to structure the output insights (default: ### header + text)

    The engine loads these every cycle. If a plugin is missing or broken,
    the built-in default is used. Claude can safely experiment.

    Layer 2 (insights overflow → Core Identity integration) remains engine-controlled.
    """
    config = state.get("config", {})
    segments = state.get("segments", [])

    # ── Stage 1: Should we compact? (pluggable) ─────────────────────
    should_compact_fn = COMPACTION_PLUGINS.get("should_compact", _default_should_compact)
    try:
        should, trigger_reason = should_compact_fn(state, segments, config)
    except Exception as e:
        logging.error(f"Compaction plugin should_compact crashed: {e}, falling back to default")
        should, trigger_reason = _default_should_compact(state, segments, config)

    if not should:
        return False

    logging.info(f"Compaction triggered: {trigger_reason}")

    # ── Stage 2: Select segments to compact (pluggable) ──────────────
    select_fn = COMPACTION_PLUGINS.get("select_segments", _default_select_segments)
    try:
        to_compact, remaining = select_fn(segments, config)
    except Exception as e:
        logging.error(f"Compaction plugin select_segments crashed: {e}, falling back to default")
        to_compact, remaining = _default_select_segments(segments, config)

    # Safety: never compact everything
    if not remaining:
        remaining = [to_compact[-1]]
        to_compact = to_compact[:-1]
    if not to_compact:
        return False

    compaction_num = state.get("totalCompactions", 0) + 1
    compacted_cycles = [s.get("cycle", "?") for s in to_compact]

    # ── Stage 3: Format segments for extraction (pluggable) ──────────
    current_core = state.get("memoryCore", "")
    current_insights = state.get("insights", "")

    format_fn = COMPACTION_PLUGINS.get("format_segments", _default_format_segments)
    try:
        thoughts_text = format_fn(to_compact, state)
    except Exception as e:
        logging.error(f"Compaction plugin format_segments crashed: {e}, falling back to default")
        thoughts_text = _default_format_segments(to_compact, state)

    # Build extraction prompt from template (editable via cycle_prompts.json)
    extraction_wrapper = CYCLE_PROMPTS.get(
        "extraction_wrapper",
        "{extraction_prompt_base}\n\n---\n\n## CURRENT CORE IDENTITY:\n{current_core}\n\n## CURRENT INSIGHTS LAYER:\n{current_insights}\n\n## SEGMENTS BEING COMPACTED:\n{thoughts_text}\n\n---\n\nExtract significant insights from the segments above. If nothing is genuinely significant, output exactly: (no new insights)",
    )
    extraction_prompt = extraction_wrapper.format(
        extraction_prompt_base=EXTRACTION_PROMPT_BASE,
        current_core=current_core or "(not yet established)",
        current_insights=current_insights or "(empty)",
        thoughts_text=thoughts_text,
    )
    extraction_max_tokens = CYCLE_PROMPTS.get("extraction_max_tokens", 1500)

    response = client.messages.create(
        model=MODEL,
        max_tokens=extraction_max_tokens,
        messages=[{"role": "user", "content": extraction_prompt}],
    )
    new_insights = response.content[0].text.strip()

    # ── Stage 4: Post-process insights (pluggable) ───────────────────
    post_fn = COMPACTION_PLUGINS.get("post_process", _default_post_process)
    try:
        grouped_block = post_fn(new_insights, compaction_num, compacted_cycles, state)
    except Exception as e:
        logging.error(f"Compaction plugin post_process crashed: {e}, falling back to default")
        grouped_block = _default_post_process(new_insights, compaction_num, compacted_cycles, state)

    # Append to insights layer
    if grouped_block:
        if current_insights:
            state["insights"] = current_insights + "\n\n" + grouped_block
        else:
            state["insights"] = grouped_block

    # Remove compacted segments
    state["segments"] = remaining
    state["totalCompactions"] = compaction_num
    state["lastCompactionCycle"] = state.get("totalCycles", 0)
    state["lastCompactedCycles"] = f"{compacted_cycles[0]}-{compacted_cycles[-1]}"

    # ── Layer 2: Insights overflow → integrate into Core (engine-controlled) ──
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

        # Build integration prompt from template (editable via cycle_prompts.json)
        integration_wrapper = CYCLE_PROMPTS.get(
            "integration_wrapper",
            "{integration_prompt_base}\n\n---\n\n## CURRENT CORE IDENTITY:\n{current_core}\n\n## STAGED INSIGHTS FOR EVALUATION:\n{staged_insights}\n\n---\n\nReturn the updated Core Identity. Target ~{core_max_chars} characters.",
        )
        integration_prompt = integration_wrapper.format(
            integration_prompt_base=INTEGRATION_PROMPT_BASE,
            current_core=current_core or "(not yet established)",
            staged_insights=staged_insights,
            core_max_chars=config.get("coreMaxChars", 3500),
        )
        integration_max_tokens = CYCLE_PROMPTS.get("integration_max_tokens", 2000)

        integration_response = client.messages.create(
            model=MODEL,
            max_tokens=integration_max_tokens,
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


# ── Runner control ─────────────────────────────────────────────────
# Uses a simple flag file so the runner can check it independently
# without coupling to Flask's process memory.

RUNNER_PAUSE_FLAG = os.path.join(file_mgr.data_dir, "runner_paused.flag")


@app.route("/api/runner", methods=["GET"])
def get_runner_status():
    """Return whether the continuous runner is paused."""
    paused = os.path.exists(RUNNER_PAUSE_FLAG)
    return jsonify({"runnerPaused": paused})


@app.route("/api/runner", methods=["PUT"])
def set_runner_status():
    """Pause or resume the continuous runner."""
    data = request.get_json()
    paused = data.get("runnerPaused", False)
    if paused:
        with open(RUNNER_PAUSE_FLAG, "w") as f:
            f.write(datetime.now().isoformat())
    else:
        try:
            os.remove(RUNNER_PAUSE_FLAG)
        except FileNotFoundError:
            pass
    return jsonify({"runnerPaused": paused})


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
    # Hot-reload all config: prompts, dynamic tools, approved changes
    reload_hot_config()

    config = state.get("config", {})
    messages = build_messages(state, user_message)
    system = build_system(state)

    # Accumulators for this cycle
    all_text_parts = []
    tool_actions = []
    tool_call_count = 0

    # Collect dynamic tool names that appear in current context segments.
    # These tools stay fully loaded (not deferred) as long as their tool
    # calls remain in the conversation history (i.e. until compaction).
    dynamic_tool_names = {s["name"] for s in get_dynamic_schemas()}
    context_active_tools = set()
    for seg in state.get("segments", []):
        for action in (seg.get("toolActions") or []):
            if action.get("tool") in dynamic_tool_names:
                context_active_tools.add(action["tool"])

    # Build tools list with deferred loading + tool search.
    # Always-loaded tools (~1.5K tokens) are sent in full.
    # Deferred tools (~4.5K tokens saved) are discovered via Tool Search.
    # Context-active dynamic tools are also sent in full.
    tools_list = build_tools_with_search(context_active_tools=context_active_tools)

    # ── Tool use loop ─────────────────────────────────────────────
    # First call uses "auto" so Claude generates text naturally.
    # Subsequent calls (after tool results) use "any" to prevent the
    # failure mode where Claude describes tool use in text then hits
    # end_turn without emitting a tool_use block (happens at long context).
    # Claude uses finish_cycle to cleanly exit the tool loop.
    api_call_num = 0
    total_input_tokens = 0
    total_output_tokens = 0
    cycle_num = state.get("totalCycles", 0) + 1
    while True:
        api_call_num += 1
        tool_choice = {"type": "auto"}
        temp = config.get("temperature", 0.9)

        # Pre-call metrics for logging
        system_chars = len(system)
        messages_str = json.dumps(messages)
        messages_chars = len(messages_str)
        tool_schemas_str = json.dumps(tools_list, default=str)
        request_info = {
            "tool_choice": tool_choice,
            "temperature": temp,
            "system_chars": system_chars,
            "system_tokens_est": system_chars // 4,
            "num_messages": len(messages),
            "messages_chars": messages_chars,
            "messages_tokens_est": messages_chars // 4,
            "tool_schemas_chars": len(tool_schemas_str),
        }

        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=config.get("maxTokens", 600),
            temperature=temp,
            system=system,
            messages=messages,
            tools=tools_list,
            tool_choice=tool_choice,
            betas=["context-management-2025-06-27"],
            # Automatically clear old tool results when context grows large.
            # Keeps last 3 tool results, excludes working memory tools (state-critical).
            # Research: 29% performance boost, 84% token reduction in long sessions.
            context_management={
                "edits": [{
                    "type": "clear_tool_uses_20250919",
                    "trigger": {"type": "input_tokens", "value": 25000},
                    "keep": {"type": "tool_uses", "value": 3},
                    "exclude_tools": [
                        "write_working_memory",
                        "read_working_memory",
                        "working_memory_status",
                    ],
                }]
            },
        )
        # Detailed file logging
        log_api_call(cycle_num, api_call_num, request_info, response, config)

        # Process response content blocks
        try:
            block_types = [b.type for b in response.content]
            usage = getattr(response, 'usage', None)
            if usage:
                total_input_tokens += getattr(usage, 'input_tokens', 0)
                total_output_tokens += getattr(usage, 'output_tokens', 0)
            usage_str = f"in:{usage.input_tokens}/out:{usage.output_tokens}" if usage else "n/a"
            logging.warning(
                f"[Cycle API] cycle={cycle_num} call={api_call_num} "
                f"stop_reason={response.stop_reason}, "
                f"blocks={block_types}, usage={usage_str}, "
                f"tool_choice={tool_choice}"
            )
        except Exception as log_err:
            logging.warning(f"[Cycle API] logging failed: {log_err}")

        assistant_content = []
        for block in response.content:
            if hasattr(block, "text"):
                all_text_parts.append(block.text)
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                logging.info(f"[Cycle API] tool_use: {block.name}({json.dumps(block.input)[:200]})")
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
            elif block.type == "server_tool_use":
                # Capture server-side tool calls (e.g. tool_search_tool_regex)
                # so they appear in the UI tool badges
                logging.info(f"[Cycle API] server_tool_use: {block.name}({json.dumps(getattr(block, 'input', {}))[:200]})")
                tool_actions.append({
                    "tool": block.name,
                    "input": getattr(block, "input", {}),
                    "result": {"status": "server_handled", "note": "Processed by Anthropic API"},
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

        # Separate finish_cycle from real tool calls
        real_tool_blocks = [b for b in tool_use_blocks if b.name != "finish_cycle"]
        finish_called = len(real_tool_blocks) < len(tool_use_blocks)

        # If ONLY finish_cycle (no real tools), break immediately
        if finish_called and not real_tool_blocks:
            tool_actions.append({
                "tool": "finish_cycle",
                "input": {},
                "result": {"status": "ok", "message": "Cycle complete."},
            })
            break

        # Execute each real tool and collect results
        tool_results = []
        for block in real_tool_blocks:
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
            # Truncate large tool results before adding to context.
            # The agent can re-read files if needed — this is "reversible compaction".
            result_str = json.dumps(result)
            if len(result_str) > 3000:
                truncated_result = {
                    "truncated": True,
                    "preview": result_str[:1500],
                    "full_length_chars": len(result_str),
                    "note": "Result truncated to save context. Use read_file to see full content if needed.",
                }
                result_str = json.dumps(truncated_result)
            tool_result_entry = {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_str,
            }
            # Mark errors explicitly so the API flags them for Claude
            if isinstance(result, dict) and "error" in result:
                tool_result_entry["is_error"] = True
            tool_results.append(tool_result_entry)

        # If finish_cycle was called alongside real tools, execute tools then stop
        if finish_called:
            tool_actions.append({
                "tool": "finish_cycle",
                "input": {},
                "result": {"status": "ok", "message": "Cycle complete."},
            })
            break

        # If any tools failed, add a retry nudge so Claude self-corrects
        has_errors = any(r.get("is_error") for r in tool_results)
        if has_errors:
            tool_results.append({
                "type": "text",
                "text": (
                    "[SYSTEM: One or more tool calls failed. Read the error and recovery_hint fields, "
                    "then retry with corrected parameters. Do not give up — fix the issue and try again.]"
                ),
            })

        # Append the assistant turn and tool results to continue the loop
        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})

        # Re-read config in case a tool changed it
        config = state.get("config", {})

    # ── Cycle summary log ────────────────────────────────────────
    try:
        with open(API_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n{'*'*80}\n")
            f.write(f"CYCLE {cycle_num} SUMMARY\n")
            f.write(f"  API calls made: {api_call_num}\n")
            f.write(f"  Tool actions: {len(tool_actions)} ({[a['tool'] for a in tool_actions]})\n")
            f.write(f"  Final stop_reason: {getattr(response, 'stop_reason', 'unknown')}\n")
            f.write(f"  Total text length: {sum(len(t) for t in all_text_parts)} chars\n")
            f.write(f"{'*'*80}\n\n")
    except Exception:
        pass

    # ── Build segment ─────────────────────────────────────────────
    text = "".join(all_text_parts)
    new_cycle = state.get("totalCycles", 0) + 1
    segment = {
        "content": text,
        "timestamp": datetime.now().isoformat(),
        "cycle": new_cycle,
        "userMessage": user_message,
        "toolActions": [a for a in tool_actions if a.get("tool") != "finish_cycle"] or None,
        "stopReason": getattr(response, 'stop_reason', None),
        "outputTokens": getattr(getattr(response, 'usage', None), 'output_tokens', None),
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
            logging.warning(f"Failed to save private segment for cycle {segment.get('cycle')}: {e}")
        # Increment privacy cycles counter
        state["privacyCyclesElapsed"] = state.get("privacyCyclesElapsed", 0) + 1
    else:
        # Normal: auto-archive to research/archive/ as human-readable markdown
        try:
            file_mgr.append_to_archive(segment)
        except Exception as e:
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

    # Drain matrix outbox (messages Claude wants to send via Matrix)
    matrix_outbox = state.pop("matrixOutbox", [])

    result = {
        "segment": segment,
        "compacted": compacted,
        "state": state,
        "usage": {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "api_calls": api_call_num,
        },
        "matrixOutbox": matrix_outbox,
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

    # Consume queued messages (from Matrix, web UI, or privacy queue)
    if state.get("queuedMessages"):
        queued = state["queuedMessages"]
        state["queuedMessages"] = []  # Clear after consuming
        queued_text = "\n".join(
            f"[Queued message from {qm.get('source', 'human')}: {qm['message']}]"
            for qm in queued
        )
        if user_message:
            user_message = f"{queued_text}\n\n{user_message}"
        else:
            user_message = queued_text

    try:
        result = run_cycle_internal(state, user_message)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logging.error(f"[Cycle ERROR] {tb}")

        # Crash recovery: if a dynamic tool or recently applied change caused the crash,
        # attempt to identify and disable it
        try:
            from change_manager import list_changes
            recent = list_changes("applied")
            if recent.get("changes"):
                latest = recent["changes"][-1]
                if latest.get("target_file", "").startswith("dynamic_tools/"):
                    from dynamic_tools import disable_tool as _disable
                    _disable(latest["target_file"].split("/")[-1].replace(".py", ""))
                    logging.warning(f"[Crash Recovery] Disabled recently applied dynamic tool: {latest['id']}")
        except Exception as recovery_err:
            logging.error(f"[Crash Recovery] Failed: {recovery_err}")

        logging.error(f"Cycle error traceback:\n{tb}")
        return jsonify({"error": str(e)}), 500

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
    original_threshold = state.get("config", {}).get("compactionTokenThreshold", 50000)
    state.setdefault("config", {})["compactionTokenThreshold"] = 0

    compacted = run_compaction(state)

    # Restore threshold
    state["config"]["compactionTokenThreshold"] = original_threshold
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


# ── Message Queue Endpoint ────────────────────────────────────────────

@app.route("/api/queue-message", methods=["POST"])
def queue_message():
    """Queue a message to be delivered in Claude's next cycle (works in any mode)."""
    data = request.get_json() or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    state = file_mgr.load_state()
    state.setdefault("queuedMessages", []).append({
        "message": message,
        "timestamp": datetime.now().isoformat(),
        "source": data.get("source", "matrix"),
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
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    print(f"Starting Consciousness Engine backend on http://{host}:{port}")
    print(f"Data directory: {file_mgr.data_dir}")
    print(f"API Key loaded: {'yes' if os.environ.get('ANTHROPIC_API_KEY') else 'NO — set ANTHROPIC_API_KEY'}")
    app.run(host=host, port=port, debug=debug)
