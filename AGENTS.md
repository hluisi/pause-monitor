# AGENTS.md - rogue-hunter

This file is the startup contract for agents working in `/Users/hunter/Projects/rogue-hunter`.

## Session Startup (Required)

Before doing anything else:
1. Confirm active Serena project via `get_current_config`.
2. Set `session_home_project` to the active Serena project at startup.
3. If no active project exists, ask the user to choose/create one and activate it.
4. Read `idx-home`.
5. Load only task-relevant memories.

## Project Scope Safety (Mandatory)

- Home anchor is dynamic: `session_home_project`.
- For cross-project work, follow `sop-project-scope-switch`.
- Prefer full project paths when activating targets.
- After cross-project work, reactivate `session_home_project` and verify before writing session-home memories or finalizing.

## Memory Policy

- Serena is the primary memory system.
- Follow `standard-memory` for naming and metadata.
- Follow `sop-memory-write` for all writes.
- Use `template-memory-entry` for new non-index memories.
- Update `catalog-memory` after every memory write.
- Use `misc-<domain>-<topic>` for true edge cases.
- Treat `.serena/memories/*.md` as managed memory objects, not regular docs.
- For memory changes, use Serena memory tools only: `read_memory`, `edit_memory`, `write_memory`, `delete_memory`.
- Before any memory write, verify memory tools are active (`no-memories` mode must not be active).
- Do not use file-edit tools (`apply_patch`, shell redirection, direct file writes) on `.serena/memories/*.md`.

## Project Context

- System health monitoring daemon for diagnosing intermittent macOS system pauses.

## Commands
```bash
# Development
uv sync
uv run ruff check . && uv run ruff format .
uv run pytest

# Runtime
uv run rogue-hunter daemon
uv run rogue-hunter tui
uv run rogue-hunter status
uv run rogue-hunter events
uv run rogue-hunter config
uv run rogue-hunter install
```

## Architecture

| Module | Purpose |
| -------- | --------- |
| `cli.py` | Click-based CLI commands |
| `config.py` | Configuration loading/saving (TOML) |
| `daemon.py` | Background sampler with adaptive intervals |
| `collector.py` | Metrics collection via powermetrics |
| `stress.py` | Multi-factor stress scoring |
| `forensics.py` | Pause event capture (spindump, tailspin, logs) |
| `storage.py` | SQLite operations with auto-pruning |
| `notifications.py` | macOS notification center alerts |
| `sleepwake.py` | Sleep/wake detection via pmset |
| `tui/` | Textual-based dashboard |

## Data Locations

| Purpose | Path |
| --------- | ------ |
| Config | `~/.config/rogue-hunter/config.toml` |
| Database | `~/.local/share/rogue-hunter/data.db` |
| Events | `~/.local/share/rogue-hunter/events/` |

## Agent Discipline

### Never Write Stubs
- Stubs are bugs, not placeholders.
- Do not ship placeholder behavior such as `pass`, `...`, `raise NotImplementedError`, placeholder return values, or “implement later” TODOs that stand in for real logic.
- If you cannot fully implement a feature, do not add partial/stubbed structure.
- If dependency/scope blocks implementation, stop and ask the user.

Before claiming done, search for `TODO`, `FIXME`, `pass`, `...`, `NotImplementedError`, and placeholder return values introduced by your work.

### Zero Linter Errors
Before claiming completion, run the lint/format command listed in `Commands`.

### No “Not My Problem” Dismissals
- If you encounter warnings, deprecations, or nearby defects while working, address them instead of deferring by default.

## Project Knowledge

| Memory | Purpose |
| -------- | --------- |
| `design_spec` | What SHOULD exist (canonical spec) |
| `implementation_guide` | What DOES exist and how (includes design decisions) |
| `unimplemented_features` | What's MISSING or incomplete |

Deep audits can use the `auditing-codebase` skill from `.claude/skills/` when available.

## Reference Docs

| Library | Location | Update |
| ------- | -------- | ------ |
| Textual | `vendor/textual-docs/docs/` | `cd vendor/textual-docs && git pull` |

These references are sparse-cloned from upstream repositories and gitignored.
