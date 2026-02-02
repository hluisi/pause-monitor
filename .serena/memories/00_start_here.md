# Start Here

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

## What This Project Is

**Rogue Hunter** is a real-time process surveillance tool for macOS that identifies processes consuming disproportionate system resources. It continuously monitors all running processes, calculating each process's share of CPU, GPU, memory, disk I/O, and wakeups. Processes with disproportionate resource usage (>15% of any resource) get elevated scores, and forensic data is captured automatically when thresholds are crossed.

## Quick Reference

| To understand... | Read |
|------------------|------|
| What this project does | `project_overview` |
| Directory structure | `project_structure` |
| Code style and patterns | `style_and_conventions` |
| Useful commands | `suggested_commands` |
| How to complete tasks | `task_completion` |
| Architectural systems | `systems` |

## Domain Knowledge

| To understand... | Read |
|------------------|------|
| What SHOULD exist (spec) | `design_spec` |
| What DOES exist (implementation) | `implementation_guide` |
| What's MISSING | `unimplemented_features` |
| Database schema | `data_schema` |
| Patterns and decisions | `insights` |
| All memories | `memory_index` |

## Key Systems

- **Configuration** (`config.py`): Hierarchical TOML config, XDG paths
- **Storage** (`storage.py`): SQLite with WAL, schema v18
- **Collector** (`collector.py`): libproc-based metrics, `ProcessScore` schema
- **Tracker** (`tracker.py`): Event lifecycle (entry/checkpoint/exit snapshots)
- **Socket IPC** (`socket_server.py`): Real-time streaming to TUI

## First Steps

1. Read this file
2. Check `task_completion` before finishing any work
3. Follow patterns in `style_and_conventions`
4. Run `uv run ruff check . && uv run ruff format .` before claiming done
