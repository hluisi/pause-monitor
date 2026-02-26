---
id: standard-memory
type: standard
domain: agent
subject: memory-naming-and-governance
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: []
tags: [standard, memory, naming, metadata, scope]
related: [idx-home, sop-memory-write, sop-project-scope-switch, catalog-memory]
sources: []
---

## Naming Grammar
- Primary: `<prefix>-<domain>-<topic>`
- Date exceptions: `log-daily-YYYY-MM-DD`, `log-session-YYYY-MM-DD-HHMM-<slug>`, `decision-YYYY-MM-DD-<slug>`, `incident-YYYY-MM-DD-<slug>`
- Lowercase + hyphen only.

## Standard Prefixes
`idx`, `catalog`, `taxonomy`, `alias`, `glossary`, `map`, `ref`, `runbook`, `howto`, `sop`, `api`, `schema`, `config`, `env`, `security`, `network`, `storage`, `integration`, `architecture`, `pattern`, `command`, `troubleshooting`, `checklist`, `template`, `policy`, `standard`, `compliance`, `decision`, `adr`, `rfc`, `spec`, `plan`, `task`, `milestone`, `risk`, `assumption`, `dependency`, `research`, `compare`, `benchmark`, `evaluation`, `experiment`, `poc`, `sourcepack`, `log-daily`, `log-session`, `log-change`, `log-run`, `incident`, `postmortem`, `audit`, `release`, `agent-profile`, `agent-policy`, `agent-playbook`, `agent-map`, `draft`, `superseded`, `deprecated`, `archive`, `misc`.

## Metadata Header
All memories except `idx-*` and `template-*` must include:
`id`, `type`, `domain`, `subject`, `status`, `created`, `updated`, `review_after`, `owner`, `aliases`, `tags`, `related`, `sources`.

## Edge Cases
Use `misc-<domain>-<topic>` with `classification_note`, `candidate_prefixes`, `reclassify_after`.
