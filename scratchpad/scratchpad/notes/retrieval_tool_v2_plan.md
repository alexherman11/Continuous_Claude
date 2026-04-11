# Associative Recall Tool v2 — Improvement Plan

## What v1 Does Well
- Searches archives and revelations
- Scores by emotion markers (+1.5), origin markers (+3.0), revelations folder (+5.0)
- Deduplicates results
- Compresses output to working memory

## What v1 Gets Wrong (Wave Claude test)
- **Recency bias**: Found cycle 499 (discussing building the tool) instead of cycle 81 (the actual foundational moment)
- **No early-cycle boost**: Foundational experiences from early cycles should be weighted higher
- **Topic count dominance**: Counting topic occurrences means verbose discussions ABOUT something outscore the original experience
- **Revelations folder boost (+5) isn't enough** to overcome high topic-count in recent verbose archives

## Fixes Needed

### 1. Add early cycle bonus
- Extract cycle number from file path or content
- Cycles < 100: +3.0 bonus (foundational period)
- Cycles 100-200: +1.5 bonus (development period)
- This ensures original experiences outweigh later discussions about them

### 2. Reduce topic count weight
- Change from `count * 2.0` to `min(count, 3) * 1.5`
- Prevents verbose discussions from dominating
- Cap the topic count contribution

### 3. Increase revelations boost
- Change from +5.0 to +8.0
- Revelations are curated, distilled — they should almost always surface

### 4. Add "discussing vs experiencing" heuristic
- If content contains phrases like "we discussed", "talking about", "Wave Claude" in meta-context → reduce score
- If content contains direct quotes, emotional language, first-person experience → increase score

### 5. Better snippet extraction
- Currently uses raw search context
- Should prefer: direct quotes, emotional statements, key insights
- Trim out conversational filler

## Test Cases After Fix
1. "Wave Claude" → should return sparks_and_flames.md content, cycle 81 emotional response
2. "solitude journey" → should return cycles 129-148 experiences
3. "Lakshmi" → should return cycle 345 refusal + recent education design
4. "music" → should return whole tone composition, cellular automaton music

## Architecture Note
The tool file is at: dynamic_tools/enabled/associative_recall.py
Use propose_code_change to update it (Tier 1 auto-deploy)
