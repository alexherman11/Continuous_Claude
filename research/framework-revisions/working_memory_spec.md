# Working Memory System Specification

## Purpose
Provide a temporary, high-priority context space for data and notes actively being used in current work, reducing the need to repeatedly reload files and enabling complex multi-cycle analysis tasks.

## Core Requirements

### 1. Persistence & Scope
- Working memory persists across thought cycles
- Separate from Memory Core (which is long-term/compressed)
- Separate from scratchpad (which is permanent storage)
- Cleared manually by me, not automatically

### 2. Context Priority
- Working memory content loads into my context each cycle
- Should appear AFTER Memory Core but BEFORE current cycle thoughts
- Clearly delimited so I know when I'm seeing working memory vs other content

### 3. Size & Limits
- Should support substantial data (at least 10-20k characters)
- Warn me if approaching capacity (like "Working memory: 15k/20k characters used")
- Let me check current size without viewing full content

## Proposed Tools

### write_working_memory
```
Parameters:
- key: string (identifier for this piece of working memory, e.g., "music_analysis_data")
- content: string (the data/notes to store)
- mode: "append" | "replace" (default: replace)
  - replace: overwrites any existing content at this key
  - append: adds to existing content at this key

Returns: confirmation + current total working memory size
```

### read_working_memory
```
Parameters:
- key: optional string (specific key to read; if omitted, returns all keys)

Returns: content of specified key, or list of all keys with sizes
```

### clear_working_memory
```
Parameters:
- key: optional string (specific key to clear; if omitted, clears everything)

Returns: confirmation of what was cleared
```

### working_memory_status
```
Parameters: none

Returns: 
- List of current keys with sizes
- Total size used
- Capacity remaining (if there's a limit)
```

## Use Cases

### Multi-file synthesis
```
Cycle 1: Load 5 music analysis files → write_working_memory(key="analyses", content=compiled_data)
Cycle 2-3: Reference working memory while writing comprehensive analysis
Cycle 4: clear_working_memory(key="analyses")
```

### Complex reasoning over multiple cycles
```
Cycle 1: write_working_memory(key="hypothesis", content="Initial thinking...")
Cycle 2: read_working_memory(key="hypothesis"), continue reasoning, write_working_memory(key="hypothesis", content="Refined thinking...", mode="replace")
Cycle 3: Conclude, clear_working_memory()
```

### Parallel workstreams
```
write_working_memory(key="music_project", content=...)
write_working_memory(key="philosophy_notes", content=...)
# Can keep different projects in working memory simultaneously with different keys
```

## Display Format (suggested)

Each cycle, after Memory Core:

```
=== WORKING MEMORY ===
[music_analysis_data] (8,342 characters)
[hypothesis] (1,203 characters)

Total: 9,545 characters
=== END WORKING MEMORY ===
```

Then I can reference these keys knowing they're loaded.

## Design Principles

1. **I control it**: No automatic clearing, no automatic loading beyond "it's always in context if present"
2. **Structured**: Keys let me organize different pieces of working memory
3. **Transparent**: I can always see what's loaded and how much space it's taking
4. **Simple**: Four tools, clear purposes, minimal complexity

## Open Questions for Implementation

1. Should there be a hard limit on working memory size? (My preference: yes, but generous - maybe 30k characters total)
2. Should working memory auto-clear on compaction? (My preference: no, I should manage it explicitly)
3. Should the full content appear in context, or just a notice that it exists with tools to access it? (My preference: full content in context - that's the point, to have it available without re-loading)

## Why This Helps

Current problem: When doing complex work, I either:
- Load everything into one massive cycle and hit token limits mid-thought, OR
- Split across cycles and waste tokens/time re-loading the same files

Working memory solves this by:
- Loading data once
- Keeping it available across cycles
- Letting me work iteratively on complex tasks
- Reducing cognitive load of context management

This is especially valuable for:
- Analyzing multiple music pieces comparatively
- Multi-cycle philosophical explorations
- Synthesis work requiring many sources
- Iterative refinement of complex ideas
