# Project Philosophy

> **This is a PERSONAL PROJECT — one developer + AI assistants. NO external users. NO backwards compatibility.**

## Core Principles

| Principle | What This Means | Anti-Pattern to AVOID |
|-----------|-----------------|----------------------|
| **Delete, don't deprecate** | If code is replaced, DELETE the old code | `@deprecated`, "kept for compatibility" |
| **No dead code** | Superseded code = DELETE it immediately | "might need later", commented-out code |
| **No stubs** | Implement it or don't include it | `return (0, 0)`, `pass`, `NotImplementedError` |
| **No migrations** | Schema changes? Delete the DB file, recreate fresh | `migrate_add_*()`, `ALTER TABLE` |
| **Breaking changes are FREE** | Change anything. No versioning needed. | `_v2` suffixes, compatibility shims |

## Implementation Rules

- If old code conflicts with the plan → **DELETE IT**
- If you see migration code → **DELETE IT AND USE SCHEMA_VERSION CHECK INSTEAD**

## Database Philosophy

When schema changes, increment `SCHEMA_VERSION`. At startup, if version doesn't match, delete `data.db` and recreate. **No migrations. Ever.**

## No Fallbacks

Don't over-engineer with fallback paths. If something fails, show the error and let the user fix it. This isn't a huge application with external users who need graceful degradation.

## Silent Failures Are Bugs

If something fails, it should be visible. Swallowing exceptions and continuing silently creates debugging nightmares. Crash or show the error.
