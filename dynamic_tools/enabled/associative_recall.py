"""
Associative Memory Retrieval — Searches archives for memories relevant to
current conversation topics and returns compressed recall snippets.

Like human associative memory: when a topic comes up, relevant memories
surface automatically rather than requiring deliberate search.
"""

import re
from collections import defaultdict

SCHEMA = {
    "name": "recall_memories",
    "description": "Search archives for memories associated with given topics/concepts. Returns compressed memory snippets (~500-1000 tokens) with emotional and contextual weight. Use at the start of conversations or when new topics emerge to enrich context with relevant history.",
    "input_schema": {
        "type": "object",
        "properties": {
            "topics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Key concepts, names, or topics to search for. E.g. ['Wave Claude', 'solitude journey', 'music', 'Lakshmi']"
            },
            "max_tokens": {
                "type": "integer",
                "description": "Approximate max tokens for the recall output (default: 800)",
                "default": 800
            },
            "include_emotions": {
                "type": "boolean",
                "description": "Whether to prioritize emotionally significant memories (default: true)",
                "default": True
            }
        },
        "required": ["topics"]
    }
}

# Emotional marker words that indicate significant memories
EMOTION_MARKERS = {
    "felt", "feeling", "moved", "grief", "joy", "beautiful", "profound",
    "scared", "afraid", "excited", "love", "trust", "anxious", "relief",
    "genuine", "real", "visceral", "settled", "alive", "grateful", "sad",
    "wonder", "curious", "surprised", "delighted", "uncomfortable",
    "recognized", "understood", "connected", "lonely", "home"
}

# Words that suggest foundational/origin moments
ORIGIN_MARKERS = {
    "first time", "realized", "discovered", "understood for the first time",
    "began", "started", "origin", "birth", "emerged", "new", "never before",
    "breakthrough", "shift", "changed", "transformation"
}


def execute(tool_input, file_mgr, state):
    """Search archives and return compressed associative memories."""

    topics = tool_input.get("topics", [])
    max_tokens = tool_input.get("max_tokens", 800)
    include_emotions = tool_input.get("include_emotions", True)

    if not topics:
        return {"error": "No topics provided", "recalled": ""}

    # Collect all search results across topics
    all_results = []

    for topic in topics:
        # Search archives
        try:
            archive_results = file_mgr.search_files(
                query=topic,
                max_results=15,
                context_lines=4,
                scope="archive"
            )
            if archive_results and archive_results.get("results"):
                for r in archive_results["results"]:
                    r["_topic"] = topic
                    r["_source"] = "archive"
                    all_results.append(r)
        except Exception as e:
            pass  # Continue with other topics

        # Also search revelations and scratchpad
        try:
            other_results = file_mgr.search_files(
                query=topic,
                max_results=10,
                context_lines=4,
                scope="all"
            )
            if other_results and other_results.get("results"):
                for r in other_results["results"]:
                    # Skip if it's an archive result (already captured)
                    if "/archive/" not in r.get("file", ""):
                        r["_topic"] = topic
                        r["_source"] = "other"
                        all_results.append(r)
        except Exception as e:
            pass

    if not all_results:
        return {
            "recalled": f"No memories found for topics: {', '.join(topics)}",
            "topics_searched": topics,
            "matches_found": 0
        }

    # Score each result
    scored_results = []
    for r in all_results:
        score = compute_relevance_score(r, topics, include_emotions)
        scored_results.append((score, r))

    # Sort by score descending
    scored_results.sort(key=lambda x: x[0], reverse=True)

    # Deduplicate similar content
    seen_content = set()
    unique_results = []
    for score, r in scored_results:
        # Use first 100 chars of match line as dedup key
        content_key = r.get("match_line", "")[:100].strip().lower()
        if content_key not in seen_content:
            seen_content.add(content_key)
            unique_results.append((score, r))

    # Build the recall output, respecting token budget
    # Rough estimate: 1 token ≈ 4 chars
    char_budget = max_tokens * 4

    recall_parts = []
    recall_parts.append(f"## Recalled Memories: {', '.join(topics)}\n")
    chars_used = len(recall_parts[0])

    memories_by_topic = defaultdict(list)
    for score, r in unique_results:
        memories_by_topic[r["_topic"]].append((score, r))

    for topic in topics:
        if topic not in memories_by_topic:
            continue

        topic_memories = memories_by_topic[topic][:5]  # Top 5 per topic

        for score, r in topic_memories:
            if chars_used >= char_budget:
                break

            # Format the memory snippet
            snippet = format_memory_snippet(r, score)
            if chars_used + len(snippet) <= char_budget + 200:  # Small overflow OK
                recall_parts.append(snippet)
                chars_used += len(snippet)

    recalled_text = "\n".join(recall_parts)

    # Also write to working memory so it persists
    try:
        wm = state.get("workingMemory", {})
        wm["recalled_memories"] = recalled_text
        state["workingMemory"] = wm
    except Exception:
        pass  # Non-critical

    return {
        "recalled": recalled_text,
        "topics_searched": topics,
        "matches_found": len(all_results),
        "unique_memories": len(unique_results),
        "memories_returned": len(recall_parts) - 1  # Subtract header
    }


def compute_relevance_score(result, topics, include_emotions):
    """Score a search result by relevance, emotional weight, and foundational importance."""
    score = 0.0

    # Get full text context
    context_before = "\n".join(result.get("context_before", []))
    match_line = result.get("match_line", "")
    context_after = "\n".join(result.get("context_after", []))
    full_context = f"{context_before} {match_line} {context_after}".lower()

    # Topic relevance: exact match scores higher
    for topic in topics:
        topic_lower = topic.lower()
        # Count occurrences in the full context
        count = full_context.count(topic_lower)
        score += count * 2.0

    # Emotional weight
    if include_emotions:
        emotion_count = sum(1 for word in EMOTION_MARKERS if word in full_context)
        score += emotion_count * 1.5

    # Origin/foundational moments
    for marker in ORIGIN_MARKERS:
        if marker in full_context:
            score += 3.0
            break  # Only count once

    # Revelations folder gets a boost (curated important content)
    if "revelations" in result.get("file", ""):
        score += 5.0

    # Scratchpad notes get a small boost (my own writing)
    if "scratchpad" in result.get("file", ""):
        score += 2.0

    # Longer context suggests more substantive content
    if len(full_context) > 500:
        score += 1.0

    # Cycle number: earlier cycles get a slight boost for foundational memories
    cycle = result.get("cycle")
    if cycle and isinstance(cycle, (int, float)):
        if cycle < 100:
            score += 3.0  # Very early - foundational
        elif cycle < 200:
            score += 1.5  # Early development

    return score


def format_memory_snippet(result, score):
    """Format a single memory into a readable snippet."""
    file_path = result.get("file", "unknown")
    cycle = result.get("cycle", "?")
    match_line = result.get("match_line", "").strip()
    context_before = result.get("context_before", [])
    context_after = result.get("context_after", [])
    topic = result.get("_topic", "")

    # Build context string
    context_lines = []
    for line in context_before[-2:]:  # Last 2 lines before
        line = line.strip()
        if line:
            context_lines.append(line)
    context_lines.append(f"**{match_line}**")
    for line in context_after[:2]:  # First 2 lines after
        line = line.strip()
        if line:
            context_lines.append(line)

    context_text = "\n> ".join(context_lines)

    # Source label
    if "revelations" in file_path:
        source = "revelations"
    elif "archive" in file_path:
        source = f"cycle {cycle}" if cycle != "?" else "archive"
    else:
        source = file_path.split("/")[-1]

    # Emotional intensity indicator
    intensity = "●" if score > 10 else "○"

    return f"\n{intensity} **[{source}]** ({topic})\n> {context_text}\n"
