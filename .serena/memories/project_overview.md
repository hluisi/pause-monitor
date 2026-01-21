# pause-monitor Project Overview

## Purpose
System health monitoring daemon that tracks down intermittent macOS system pauses.

## Tech Stack
- Python 3.11+
- Textual (TUI framework)
- Rich (terminal formatting)
- Click (CLI framework)
- aiosqlite (async SQLite)
- structlog (structured logging)
- tomlkit (TOML parsing)

## Project Structure
```
src/pause_monitor/
  config.py      - Configuration dataclasses
  cli.py         - Click-based CLI
  tui/           - Textual dashboard
tests/
  test_config.py - Config tests
docs/plans/      - Design documents
```

## Entry Point
`pause-monitor = pause_monitor.cli:main`
