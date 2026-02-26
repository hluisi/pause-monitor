---
id: agent-playbook-subagent-best-practices
type: agent-playbook
domain: agent
subject: playbook-subagent-best-practices
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: [subagent_best_practices]
tags: []
related: []
sources: []
---

# Subagent Best Practices

Comprehensive guide for spawning and using subagents in Claude Code skills.

## Core Concept

Subagents are **context isolation tools**. Each gets a fresh context window — no conversation history, no previous tool calls. You must provide everything they need in the prompt.

## How Subagents Work

### What They Receive
- Their prompt (the full text you send)
- Working directory info
- CLAUDE.md project memory (inherited)
- Preloaded skills (if configured)

### What They DON'T Receive
- Your conversation history
- Earlier tool calls/results
- Other subagents' work (unless you summarize it)

## Spawning Patterns

### 1. Exploratory (2-3 agents)
Different perspectives on the same problem. Diminishing returns past 3.

```
| Agent | Focus | Returns |
|-------|-------|---------|
| Simplicity | DRY, readability | Issues with reasoning |
| Correctness | Bugs, edge cases | Issues with reasoning |
| Conventions | Project patterns | Issues with reasoning |
```

**Use when:** Multiple angles on the same code/problem.

### 2. Partitioned (N agents)
Same template, different data slices. Scales linearly.

```
10 categories → 10 agents
8 plan tasks → 8 agents
```

**Use when:** Independent chunks of identical work.

### 3. Sequential/Pipeline
Output of one feeds the next. Cannot parallelize.

```
Implementer → Spec Review → Code Review
```

**Use when:** Dependencies between steps.

## Prompt Template Pattern

For partitioned work, create a **parameterized template**:

```markdown
You are a [ROLE]. Your task: [TASK_DESCRIPTION]

## Context
[CONTEXT_CONTENT]

## Files to Analyze
[FILE_MANIFEST]

## What to Look For
[CATEGORY_SPECIFIC_CONTENT]

## Output Format
Return ONLY valid JSON:
{"category":"[CATEGORY_KEY]","findings":[...]}
```

### Placeholder Table
Document where each value comes from:

| Placeholder | Source |
|-------------|--------|
| `{ROLE}` | Fixed per template |
| `{CATEGORY_KEY}` | From iteration table |
| `{FILE_MANIFEST}` | Runtime: detected files |
| `{CATEGORY_CONTENT}` | From reference docs |

## Prompt Best Practices

### Give Jobs, Not Checkpoints
```markdown
# Bad (will be skipped)
"Review the code and let me know if it looks good"

# Good (will be done)  
"Find 1-3 specific issues with file:line references"
```

### Include All Context
```markdown
# Bad (assumes shared context)
"Implement task 3"

# Good (self-contained)
"Implement user authentication:
- Add login endpoint at POST /auth/login
- Use JWT tokens with 24h expiry
- Follow patterns in src/auth/register.ts"
```

### Specify Return Format
```markdown
# Bad (open-ended)
"Explore the codebase"

# Good (structured)
"Return:
- Entry points with file:line
- Key abstractions list
- 5-10 essential files to read"
```

## Return Value Handling

### Structured Summaries
Subagents should return actionable, parseable output:

```json
{
  "category": "over-engineering",
  "findings": [
    {
      "file": "src/auth.ts",
      "line": 42,
      "description": "Factory pattern for single implementation",
      "severity": "medium"
    }
  ],
  "summary": {"total": 1, "high": 0, "medium": 1, "low": 0}
}
```

### Consolidation After Return
```markdown
After agents return:
1. Parse JSON from each
2. Dedupe by (file, line)
3. Merge similar findings
4. Sort by severity
```

## Model Selection

| Model | Use For | Speed | Cost |
|-------|---------|-------|------|
| `haiku` | Fast exploration, read-only search | Fast | Low |
| `sonnet` | Balanced coding tasks | Medium | Medium |
| `opus` | Complex reasoning, security reviews | Slow | High |
| (inherit) | Same as main conversation | — | — |

**Don't specify model** unless you have a reason — subagents inherit by default.

## Parallel Dispatch

All parallel agents must be dispatched in **ONE message** with multiple Task tool calls.

```markdown
# Wrong: sequential dispatch
[Task 1] → wait → [Task 2] → wait → [Task 3]

# Right: parallel dispatch  
[Task 1, Task 2, Task 3] → all run concurrently
```

## Common Anti-Patterns

| Anti-Pattern | Fix |
|--------------|-----|
| Vague descriptions | Be specific about task and expected output |
| Missing context | Include all info subagent needs in prompt |
| No return format | Tell subagent exactly what to return |
| Too many categories per agent | One focused task per dispatch |
| Assuming shared context | Subagents start fresh — provide everything |
| Nesting subagents | Not supported — chain from main conversation |
| Returning raw output | Configure subagent to summarize |
| Limiting partitioned work to 2-3 | Match agent count to independent chunks |

## Background Execution

Use `run_in_background: true` when:
- High-volume output (tests, builds)
- You want to continue working
- Task doesn't need clarification

**Caveats:**
- Pre-approves permissions upfront
- No MCP tools available
- Can't ask clarifying questions

## Resuming Agents

Resumed agents retain full history — all tool calls, file reads, reasoning.

Use when:
- Follow-up work on same area
- Continuing interrupted task
- Building on previous findings

Agent transcripts persist at:
```
~/.claude/projects/{project}/{session}/subagents/agent-{id}.jsonl
```

## Skill Integration

### Dispatch from Controller
Controller orchestrates, never implements:
```markdown
1. Read plan → extract tasks
2. Per task: dispatch implementer (fresh context)
3. Review output (spec, then quality)
4. Continue or request fixes
```

### Provide Full Task Text
Subagents don't read plans — controller provides complete task:
```markdown
## Task
[Full task text from plan]

## Context
Design: [path]
Related files: [list]

## Deliverables
- Implementation
- Tests
- Summary
```

## Template vs Block Distinction

- **Prompt template**: The text sent TO the subagent
- **Response format**: What the subagent returns
- **Dispatch pattern**: HOW you spawn (parallel, sequential, partitioned)
- **Block**: The skill structure element that USES these

When building skills, all four may need shaping to fit the goal.
