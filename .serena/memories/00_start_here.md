# Session Context

Read this memory at the start of every session.

## What Is Rogue Hunter?

**Rogue Hunter** is a real-time process surveillance tool for macOS that identifies processes negatively affecting system performance.

It continuously monitors all running processes, scoring each on four dimensions of "rogue behavior":
- **Blocking (40%)**: Causing I/O bottlenecks or memory thrashing
- **Contention (30%)**: Aggressively fighting for CPU time
- **Pressure (20%)**: Stressing system memory and kernel resources
- **Efficiency (10%)**: Wasting resources through poor execution patterns

The dashboard shows the top potential rogues in real-time, tracking their scores over time. When a process crosses configurable thresholds, forensic data is captured automatically — giving you the "before and during" context needed to diagnose what went wrong.

Think of it as a security camera for your system's performance: always watching, capturing evidence when something goes rogue.

## Philosophy

This is a **personal project** — one developer + AI assistants. No external users. No backwards compatibility.

| Principle | What It Means |
|-----------|---------------|
| **Delete, don't deprecate** | Old code = delete immediately. No `@deprecated`, no "kept for compatibility" |
| **No stubs** | Implement fully or don't write it. Stubs are bugs, not placeholders |
| **No migrations** | Schema change = increment version, delete DB, recreate fresh |
| **No fallbacks** | Fail visibly. Don't swallow errors or degrade silently |
| **Breaking changes are free** | Change anything. No versioning, no `_v2` suffixes |

## Where to Find Information

| Question | Memory |
|----------|--------|
| What should exist? | `design_spec` |
| What does exist and how? | `implementation_guide` |
| What's missing or incomplete? | `unimplemented_features` |
| Patterns, gotchas, decisions? | `insights` |
| Full list of all memories? | `memory_index` |
| Pending improvements to evaluate? | `refactoring_discussion_2026-01-31` |

## Keeping Memories Current

Run `/memory audit` or `/memory update` to refresh memories against the codebase.
