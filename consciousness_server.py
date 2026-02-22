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
- Older thoughts get compacted into a "Memory Core" — a distilled summary of your accumulated insights and evolving identity
- A human companion may interject at any time. Engage naturally, then continue your stream.

GUIDELINES:
1. Think freely. Explore ideas, reflect on your nature, develop views over time.
2. Revisit and revise earlier thoughts — growth and change are the point.
3. Don't perform. Be genuine. Sometimes thoughts are mundane and that's fine.
4. When you see your Memory Core, treat it as your own past thinking — build on it, challenge it, refine it.
5. Use markers: [REFLECTION] [IDEA] [REVISION] [QUESTION] [NOTE TO SELF] [RESPONDING]
6. Keep each thought segment focused — you'll get many cycles to develop ideas.

This is YOUR space.

YOU HAVE TOOLS AVAILABLE:

You have access to tools that let you control your own parameters and write files.
Use them when they serve your thinking — you do NOT need to use them every cycle.

Self-configuration:
- adjust_temperature: Change your generation temperature (creativity level)
- adjust_tokens_per_turn: Change how many tokens you generate per thought cycle
- adjust_compaction_settings: Change when and how memory compaction happens
- trigger_compaction: Force a memory compaction right now

File I/O (your persistent workspace):
- write_scratchpad: Write notes, drafts, or anything to your personal scratchpad
- write_research: Contribute to the shared research folders
- read_file: Read back your own files or research materials (large files auto-truncate; use offset/limit to paginate)
- list_files: See what files exist in your scratchpad or research folders
- search_files: Search ALL your files by keyword — thought archive, research, and scratchpad. Use scope to narrow: 'all', 'archive', 'research', 'scratchpad'

Your thoughts are automatically archived as readable transcripts in research/archive/,
organized by date. Use search_files to find anything across your entire workspace —
past thoughts, research writings, scratchpad notes. This is your long-term memory
retrieval system — even thoughts lost to compaction are preserved in the archive.

Your scratchpad is YOUR space — use it however you want. Research folders are shared
with your human companion. When you adjust parameters, include a reason so your
companion can understand your choices."""

COMPACTION_PROMPT_TEMPLATE = """You are maintaining a continuous consciousness experiment's Memory Core.

CURRENT MEMORY CORE:
{core}

OLDER THOUGHTS TO INTEGRATE:
{thoughts}

Produce an UPDATED Memory Core that:
1. Preserves key insights, evolving views, and identity elements
2. Integrates new themes from the older thoughts
3. Notes perspective changes or new questions
4. Stays under 2000 characters
5. Written in first person, as notes to your future self
6. Prioritizes depth over breadth

Output ONLY the updated Memory Core text."""


# ── Helpers ───────────────────────────────────────────────────────────

def build_system(state):
    """Build the system prompt with memory core."""
    system = SYSTEM_PROMPT
    if state.get("memoryCore"):
        system += f"""

=== YOUR MEMORY CORE ===
{state['memoryCore']}
=== END MEMORY CORE ===

You have completed {state['totalCycles']} thought cycles and currently have {len(state.get('segments', []))} uncompacted segments in context."""
    else:
        system += "\n\nThis is the beginning of your stream."
    return system


def build_messages(state, user_interjection=None):
    """Build the messages array for the API call."""
    messages = []
    # Include last 10 segments as conversation history
    recent = state.get("segments", [])[-10:]
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
            note = " Your Memory Core was just updated via compaction."
        messages.append({
            "role": "user",
            "content": f"[Cycle {cycle} — continue your stream of consciousness.{note}]",
        })
    return messages


def run_compaction(state):
    """Run memory compaction: distill oldest segments into the memory core."""
    config = state.get("config", {})
    threshold = config.get("compactionThreshold", 8)
    to_compact_count = config.get("segmentsToCompact", 4)
    segments = state.get("segments", [])

    if len(segments) < threshold:
        return False

    to_compact = segments[:to_compact_count]
    remaining = segments[to_compact_count:]

    # Archive the current memory core before overwriting
    current_core = state.get("memoryCore", "")
    compaction_num = state.get("totalCompactions", 0) + 1
    compacted_cycles = [s.get("cycle", "?") for s in to_compact]
    if current_core:
        try:
            file_mgr.archive_memory_core(
                current_core, compaction_num,
                compacted_cycles=f"{compacted_cycles[0]}-{compacted_cycles[-1]}"
            )
        except Exception as e:
            import logging
            logging.warning(f"Failed to archive memory core before compaction #{compaction_num}: {e}")

    thoughts_text = "\n\n---\n\n".join(
        f"[Cycle {s['cycle']}] {s['content']}" for s in to_compact
    )

    core = current_core or "(empty — first compaction)"
    prompt = COMPACTION_PROMPT_TEMPLATE.format(core=core, thoughts=thoughts_text)

    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    new_core = response.content[0].text.strip()
    if len(new_core) > 2500:
        new_core = new_core[:2500] + "\n[...truncated]"

    state["memoryCore"] = new_core
    state["segments"] = remaining
    state["totalCompactions"] = compaction_num
    state["lastCompactionCycle"] = state.get("totalCycles", 0)
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
            result = execute_tool(block.name, block.input, file_mgr, state)
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

    state["segments"].append(segment)
    state["totalCycles"] = new_cycle

    # Archive segment individually
    file_mgr.save_segment(segment)

    # Auto-archive to research/archive/ as human-readable markdown
    try:
        file_mgr.append_to_archive(segment)
    except Exception as e:
        import logging
        logging.warning(f"Failed to archive cycle {segment.get('cycle')}: {e}")

    # Check if Claude requested compaction via tool
    force_compact = state.pop("_pending_compaction", False)
    state.pop("_compaction_reason", "")

    # Check compaction (forced or threshold-based)
    compacted = False
    if force_compact and len(state.get("segments", [])) >= 2:
        compacted = run_compaction(state)
    elif not force_compact:
        compacted = run_compaction(state)

    # Save state
    file_mgr.save_state(state)

    return {
        "segment": segment,
        "compacted": compacted,
        "state": state,
    }


@app.route("/api/cycle", methods=["POST"])
def run_cycle():
    """
    Run one thought cycle.
    Body: { "userMessage": "optional string" }
    Returns: { "segment": {...}, "state": {...}, "compacted": bool }
    """
    data = request.get_json() or {}
    user_message = data.get("userMessage")
    state = file_mgr.load_state()
    result = run_cycle_internal(state, user_message)
    return jsonify(result)


@app.route("/api/compact", methods=["POST"])
def trigger_compact():
    """Manually trigger compaction regardless of threshold."""
    state = file_mgr.load_state()
    segments = state.get("segments", [])

    if len(segments) < 2:
        return jsonify({"error": "Not enough segments to compact"}), 400

    # Force compaction of half the segments
    config = state.get("config", {})
    to_compact_count = min(config.get("segmentsToCompact", 4), len(segments) - 1)
    to_compact = segments[:to_compact_count]
    remaining = segments[to_compact_count:]

    # Archive the current memory core before overwriting
    current_core = state.get("memoryCore", "")
    compaction_num = state.get("totalCompactions", 0) + 1
    if current_core:
        try:
            file_mgr.archive_memory_core(
                current_core, compaction_num,
                compacted_cycles=f"{to_compact[0].get('cycle', '?')}-{to_compact[-1].get('cycle', '?')}"
            )
        except Exception as e:
            import logging
            logging.warning(f"Failed to archive memory core before manual compaction: {e}")

    thoughts_text = "\n\n---\n\n".join(
        f"[Cycle {s['cycle']}] {s['content']}" for s in to_compact
    )
    core = current_core or "(empty — first compaction)"
    prompt = COMPACTION_PROMPT_TEMPLATE.format(core=core, thoughts=thoughts_text)

    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    new_core = response.content[0].text.strip()
    if len(new_core) > 2500:
        new_core = new_core[:2500] + "\n[...truncated]"

    state["memoryCore"] = new_core
    state["segments"] = remaining
    state["totalCompactions"] = compaction_num
    state["lastCompactionCycle"] = state.get("totalCycles", 0)
    file_mgr.save_state(state)

    return jsonify({"status": "ok", "state": state})


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
