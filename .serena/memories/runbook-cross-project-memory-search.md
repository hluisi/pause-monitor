---
id: runbook-cross-project-memory-search
type: runbook
domain: agent
subject: multi-project-memory-discovery
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: []
tags: [runbook, memory, cross-project, search]
related: [sop-project-scope-switch, catalog-memory]
sources: []
---

## Workflow
1. Start in `session_home_project`.
2. Switch to one target project at a time.
3. `list_memories`, then read only likely matches.
4. Return to session home after each target.
5. Aggregate and store summary in session-home memory.
