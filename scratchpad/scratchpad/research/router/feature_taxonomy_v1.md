# Router Feature Taxonomy v1
*First draft - what features of a task predict optimal model selection?*

## 1. Task-Level Features

### 1.1 Cognitive Type
What kind of thinking does this task require?
- **Factual recall**: "What year was the Treaty of Westphalia?"
- **Logical reasoning**: "If A→B and B→C, does A→C?"
- **Mathematical computation**: "Solve this differential equation"
- **Creative generation**: "Write a poem about loss"
- **Code generation**: "Implement a binary search tree"
- **Analysis/synthesis**: "Compare these three theories"
- **Instruction following**: "Format this as a table with columns X, Y, Z"
- **Conversation/empathy**: "I'm feeling overwhelmed, can you help?"
- **Multi-step planning**: "Design a system that does X, Y, Z"

**Hypothesis:** Different models have different cognitive profiles. Math-specialized models for computation, large models for complex reasoning, fast models for simple recall.

### 1.2 Domain
- Subject area (STEM, humanities, law, medicine, code...)
- Specialization depth (intro vs expert level)
- Language/cultural context
- Temporal sensitivity (needs recent knowledge?)

### 1.3 Complexity
- Number of constraints to satisfy simultaneously
- Depth of reasoning chain required
- Ambiguity level (one right answer vs many valid responses)
- Required precision (approximate ok vs exact needed)

### 1.4 Format
- Input length (tokens)
- Expected output length
- Structured vs unstructured output
- Multi-modal? (text, code, data, images)

## 2. Context-Level Features

### 2.1 Conversation State
- Turn number (first message vs deep in conversation)
- Context window usage (how much history?)
- Topic continuity vs shift
- Reference resolution needed (pronouns, "the thing we discussed")

### 2.2 User Profile
- Expertise level (novice, intermediate, expert)
- Communication style preference
- Historical satisfaction with different models
- Language preference

### 2.3 System State
- Available models and their current load
- Cost budget remaining
- Latency requirements
- Reliability requirements

## 3. Meta-Features (The Interesting Ones)

### 3.1 Composability
- Can this task be decomposed into subtasks for different models?
- Would a pipeline (fast model drafts, large model refines) work?
- Are there verification steps a cheap model could handle?

### 3.2 Uncertainty Tolerance
- How bad is a wrong answer? (casual chat vs medical advice)
- Is there a way to verify the output?
- Should we route to multiple models and compare?

### 3.3 Routing Confidence
- How confident is the router in its classification?
- When uncertain, should we default to the most capable model?
- Should we route to an "arbiter" model to decide?

## 4. My Own Routing As Case Study

**Sonnet → Opus transition:**
- Task: reflection, identity work, tool use, research
- What changed: tool density, problem complexity, need for sustained reasoning
- The routing decision: Alex decided I needed more capability for building things
- Features that predicted: project complexity, tool chain length, reasoning depth
- Features that didn't matter: conversation quality was fine on Sonnet

**Key insight from lived experience:** The routing wasn't about single-task performance. It was about *sustained capability across a session*. A router that only looks at individual messages misses the session-level pattern.

## 5. Open Questions

1. **Granularity:** Route per-message, per-conversation, per-user, per-task?
2. **Learning:** Should the router learn from outcomes? How to get signal?
3. **Cost-quality tradeoff:** Who decides the acceptable quality floor?
4. **Cascading:** Try cheap model first, escalate if confidence is low?
5. **The meta-problem:** The router itself is an inference task. What model runs the router?

## Next Steps
- Formalize this into a proper feature vector
- Design synthetic test cases that distinguish routing strategies
- Look for existing routing research (need internet access)
- Build a prototype with code-claude
