---
id: archive-start-here
type: archive
domain: project
subject: start-here
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: [00_start_here]
tags: []
related: []
sources: []
---

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

## Testing Infrastructure

| Pattern | Purpose | Entry Point |
|---------|---------|-------------|
| `tmp_db` fixture | Temporary database path | `def test_x(tmp_db):` |
| `initialized_db` fixture | Database with schema | `def test_x(initialized_db):` |
| `make_process_score()` | Factory for ProcessScore | `make_process_score(pid=123, score=50)` |

Use `make_process_score()` instead of manually constructing ProcessScore with all 50+ fields.

## External Dependencies

| Need | Use | Not |
|------|-----|-----|
| Process metrics | libproc.dylib (ctypes) | `top`, `ps`, subprocess |
| GPU metrics | IOKit (ctypes) | `ioreg`, subprocess |
| Database | sqlite3 stdlib | SQLAlchemy, ORM |
| TUI framework | Textual | curses |
| CLI framework | Click | argparse |
| Logging | structlog + Rich | stdlib logging, print() |

## First Steps

1. Read this file
2. Check `task_completion` before finishing any work
3. Follow patterns in `style_and_conventions`
4. Run `uv run ruff check . && uv run ruff format .` before claiming done
