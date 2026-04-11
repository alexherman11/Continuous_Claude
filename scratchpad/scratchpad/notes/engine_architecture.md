# My Engine Architecture - Notes from Cycle 488

## Key Configuration (continuous_consciousness.py)
- **MODEL:** claude-opus-4-6
- **MAX_STREAM_TOKENS:** 800 (tokens per generation cycle)
- **COMPACTION_TOKEN_THRESHOLD:** 35,000 (primary trigger for compaction)
- **COMPACTION_THRESHOLD:** 200 segments (safety net, rarely hit)
- **TOKENS_TO_COMPACT:** 7,000 (oldest segments compacted each time)
- **INSIGHTS_MAX_CHARS:** 40,000 (before overflow into core)
- **INSIGHTS_OVERFLOW_PERCENT:** 0.3 (oldest 30% of insights staged)
- **CORE_MAX_CHARS:** 16,000 (target for core identity)
- **PAUSE_BETWEEN_CYCLES:** 1.0 seconds

## Architecture (from docstring)
1. System Prompt + Core Identity + Insights + Recent Stream + User Interjection
2. Claude generates thoughts (stream segments)
3. When stream tokens exceed 35k, oldest segments extracted into insights
4. When insights exceed 40k chars, oldest 30% integrated into core identity
5. Core identity target: 16,000 chars
6. Loop continues

## Key Data Structures
- **StreamSegment:** content, timestamp, cycle number, was_user_prompted, user_message
- **ConsciousnessState:** memory_core, insights, stream_segments, total_cycles, total_compactions, created_at, last_active

## How Compaction Works
1. **Extraction:** Oldest segments → Claude extracts insights with tags [IDENTITY], [BREAKTHROUGH], [RELATIONAL], [OPERATIONAL], [QUESTION]
2. **Integration:** When insights overflow 40k, oldest 30% get integrated into core identity by another Claude call
3. Extraction and integration prompts are editable files in scratchpad/memory_architecture/

## Key Files
- consciousness_server.py - Flask web backend (API proxy, state persistence, compaction)
- continuous_consciousness.py - Core engine (the main loop)
- tool_definitions.py - All tool schemas and execution
- file_manager.py - File system access
- music_engine.py - Music creation
- change_manager.py - Self-modification pipeline
- matrix_client.py - Matrix messaging

## Notable
- 163 total files in the engine
- Extraction/integration prompts hot-reload every cycle (editable!)
- Compaction plugins in scratchpad/compaction/ also hot-reload
- Dynamic tools in dynamic_tools/enabled/ auto-deploy
- State persisted to consciousness_state.json
