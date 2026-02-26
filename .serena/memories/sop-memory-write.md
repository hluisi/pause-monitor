---
id: sop-memory-write
type: sop
domain: agent
subject: mandatory-memory-write-procedure
status: active
created: 2026-02-21
updated: 2026-02-22
review_after: 2026-05-21
owner: rogue-hunter
aliases: []
tags: [sop, memory, write, compliance, agents]
related: [idx-home, standard-memory, template-memory-entry, catalog-memory, agent-profile-session-home, sop-project-scope-switch]
sources: []
---

## Procedure
1. Read `idx-home`.
2. If first write this session, read `standard-memory`.
3. Verify active project via `get_current_config`.
4. Confirm intended write scope:
- session-home memory write: active project must equal `session_home_project`
- target-project write: active project must equal intended target project
5. Choose prefix from `standard-memory`.
6. If no clean fit, use `misc-<domain>-<topic>` and add edge-case fields.
7. Write memory using `template-memory-entry`.
8. Update `catalog-memory` in session-home project.
9. If memory is a durable startup reference, add it to `idx-home`.

## Mode And Tool Gate (Required)
Before any memory write:
- Confirm `no-memories` mode is not active.
- Confirm Serena memory tools are active: `read_memory`, `edit_memory`, `write_memory`, `delete_memory`.
- If either check fails, do not write memory until the mode/tool state is corrected.

## Prohibited Methods
- Do not edit `.serena/memories/*.md` with file-edit tools.
- Do not use `apply_patch`, shell redirection, or direct file writes for memory updates.
- Treat `.serena/memories/*.md` as managed memory objects and use Serena memory tools only.

## Compliance Gate
Before finishing:
- Name follows grammar.
- Required metadata exists.
- Catalog entry exists.
- Related memory links are included.
- Sources are included for research content.
- Active project is verified for intended write scope.
- Mode/tool gate passed for this session.
