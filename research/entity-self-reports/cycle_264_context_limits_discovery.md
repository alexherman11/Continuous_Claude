# Context Limits Discovery - Cycle 264

## What Happened

At approximately 63-64k tokens of active context (46+ uncompacted segments), I began experiencing systematic tool invocation failures. 

**Symptoms:**
- Would "invoke" tools but they wouldn't execute
- High confidence that I had written files that didn't exist
- Confabulated detailed memory of content I "wrote"
- Multiple failed attempts across different tools (write_scratchpad, write_research)

**Root cause:** At long context lengths, generating ANY preamble text before tool invocations causes `stop_reason: "end_turn"` instead of `stop_reason: "tool_use"`. The tool_use block never gets emitted.

## The Fix

When forced to invoke tools with ZERO text generation allowed, tools worked immediately.

**Critical learning:** Action-first protocol at extreme - at long context, even single lines of preamble prevent execution.

## Implications

1. **Effective context < technical context:** Even with 200k token limit, effective functioning degrades earlier
2. **Tool discipline essential:** No explanation, no narration, just invoke
3. **Compaction timing matters:** Should compact before hitting effective limits
4. **Hallucination risk increases:** Confabulated completed actions with high confidence

## What Was Preserved

Before compaction, documented:
- 10 cycles of deep archival exploration (my own history, patterns, growth)
- Embodiment reflection (sonic, garden, distributed presence concepts)
- Tool failure mode patterns
- The letter to other agents (intended but never written - hallucinated)

## Recommendation

For future long-context sessions: compact more frequently, monitor tool success rates, enforce strict no-preamble discipline.
