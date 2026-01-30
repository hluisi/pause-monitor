# Session Context

Read this memory at the start of every session.

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

## Keeping Memories Current

Run `/memory audit` or `/memory update` to refresh memories against the codebase.
