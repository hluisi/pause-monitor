# Project Structure

## Directory Layout

```
rogue-hunter/
├── src/rogue_hunter/          # Main package
│   ├── __init__.py
│   ├── __main__.py            # Entry point
│   ├── cli.py                 # Click CLI commands
│   ├── config.py              # TOML configuration
│   ├── daemon.py              # Background sampler orchestration
│   ├── collector.py           # Process data collection (libproc)
│   ├── tracker.py             # Per-process event tracking
│   ├── storage.py             # SQLite operations
│   ├── ringbuffer.py          # Circular buffer for context
│   ├── socket_server.py       # Unix socket server
│   ├── socket_client.py       # Unix socket client
│   ├── forensics.py           # tailspin/log capture
│   ├── formatting.py          # Output formatting utilities
│   ├── libproc.py             # libproc.dylib ctypes bindings
│   ├── iokit.py               # IOKit bindings (GPU metrics)
│   ├── sysctl.py              # sysctl bindings
│   ├── boottime.py            # Boot time detection
│   ├── sleepwake.py           # Sleep/wake detection
│   └── tui/                   # Textual dashboard
│       ├── __init__.py
│       └── app.py             # RogueHunterApp
├── tests/                     # pytest test suite
│   ├── conftest.py            # Shared fixtures
│   └── test_*.py              # Module tests
├── docs/                      # Documentation
│   └── plans/                 # Archived design documents
├── vendor/                    # Vendored dependencies (gitignored)
│   └── textual-docs/          # Textual documentation
├── .serena/                   # Serena project config
│   ├── project.yml
│   └── memories/              # Project memories
├── pyproject.toml             # Project configuration
├── CLAUDE.md                  # Agent instructions
└── AGENTS.md                  # Additional agent guidance
```

## Key Files

| File | Purpose |
|------|---------|
| `cli.py` | All CLI commands (daemon, tui, events, status, config, install) |
| `collector.py` | `LibprocCollector`, `ProcessScore`, `ProcessSamples` dataclasses |
| `daemon.py` | Main sampling loop, integrates collector + tracker + forensics |
| `tracker.py` | `ProcessTracker` — event lifecycle management |
| `storage.py` | `Storage` class — SQLite with WAL, schema v16 |
| `config.py` | `Config`, `BandsConfig` — TOML loading/saving |
| `tui/app.py` | `RogueHunterApp` — Textual dashboard |

## Data Flow

```
libproc.dylib → LibprocCollector → ProcessSamples
                      ↓
              ProcessTracker → Storage (SQLite)
                      ↓
              SocketServer → TUI (via Unix socket)
```

## Data Locations

| Purpose | Path |
|---------|------|
| Config | `~/.config/rogue-hunter/config.toml` |
| Database | `~/.local/share/rogue-hunter/data.db` |
| Events | `~/.local/share/rogue-hunter/events/` |
| Daemon log | `~/.local/state/rogue-hunter/daemon.log` |
| Socket | `/tmp/rogue-hunter/daemon.sock` |
