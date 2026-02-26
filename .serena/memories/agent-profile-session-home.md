---
id: agent-profile-session-home
type: agent-profile
domain: agent
subject: dynamic-session-home-anchor
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: []
tags: [agent-profile, session-home, scope, safety]
related: [idx-home, sop-project-scope-switch]
sources: []
---

## Session Home Definition
`session_home_project` is the Serena project active when the agent session begins.

## Scope Rule
- Cross-project work must use `sop-project-scope-switch`.
- Before final response or session-home memory writes, active project must equal `session_home_project`.
