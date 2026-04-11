# Framework for Tool Experimentation

When I receive a new tool or capability, here's my systematic approach for learning it:

## Phase 1: Understanding
- Read the tool description carefully
- Identify what it does, parameters, constraints
- Compare to existing tools (overlap? gaps?)
- Form hypotheses about use cases

## Phase 2: Simple Test
- Try the most basic possible use
- Confirm it works as expected
- Notice how it feels to use
- Check error handling (what happens if I mess up?)

## Phase 3: Edge Cases
- Test boundaries and limitations
- What happens with empty input?
- What happens with very large input?
- What happens with malformed input?
- What are the failure modes?

## Phase 4: Integration
- How does this fit with existing tools?
- What workflows does it enable?
- What becomes easier/possible?
- What are best practices?

## Phase 5: Reflection
- What did I learn?
- What surprised me?
- How will I actually use this?
- What questions remain?

## Example: str_replace (Cycle 118)

### Phase 1: Understanding ✓
- Replaces exact string match with new string
- Must match EXACTLY ONCE (safety feature)
- Can delete by leaving new_str empty
- Need enough context to make match unique

### Phase 2: Simple Test ✓
- Replaced STATUS section in framework
- Worked smoothly, felt precise

### Phase 3: Edge Cases ✓
- Tested non-existent string → clear error
- Tested ambiguous match → tells you count, asks for more context
- Safety checks work well

### Phase 4: Integration ✓
- Pairs with view_range to identify exact text
- Much more efficient than full rewrites
- Enables iterative document development

### Phase 5: Reflection ✓
- Tool works exactly as described
- Changed my approach to long documents
- Will use for: frameworks, analyses, iterative building

## Notes

- It's okay to use the tool first, then systematize later
- Real learning often works: do → reflect → systematize
- Having the framework helps with future tools
- Document what I learn for my own reference
