# pause-monitor

System health monitoring daemon that tracks down intermittent macOS system pauses.

## Quick Start

```bash
uv run pause-monitor daemon    # Run daemon in foreground
uv run pause-monitor tui       # Launch TUI dashboard
uv run pause-monitor status    # Quick health check
```

## Development

```bash
uv sync                        # Install dependencies
uv run pytest                  # Run tests
uv run ruff check .            # Lint
uv run ruff format .           # Format
```

## Architecture

| Module | Purpose |
|--------|---------|
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

## Key Design Decisions

### Stress Scoring Over CPU Thresholds

High CPU alone doesn't indicate problems. A process using 200% CPU on a 16-core machine is fine if load average is low.

The stress score combines:
- **Load/cores ratio** - Are processes queuing? (load_avg > core_count = bad)
- **I/O wait** - Are processes blocked on disk?
- **Memory pressure** - Is the system compressing/swapping?
- **Self-latency** - Did our own sleep take longer than expected?

### Adaptive Sampling

- **Normal:** 5s intervals (low overhead)
- **Elevated (stress > 30):** 1s intervals (catch the buildup)
- **Critical (stress > 60):** Preemptive snapshot (capture before freeze)

### Pause Detection

Uses `time.monotonic()` to detect when the system was unresponsive. If the actual interval between samples exceeds 2x the expected interval, a pause occurred.

### Forensics

On pause detection, immediately capture:
1. Full process snapshot via psutil
2. `spindump` for thread stacks
3. Filtered system logs from the pause window

## Data Locations

| Purpose | Path |
|---------|------|
| Config | `~/.config/pause-monitor/config.toml` |
| Database | `~/.local/share/pause-monitor/data.db` |
| Events | `~/.local/share/pause-monitor/events/` |
| Daemon log | `~/.local/share/pause-monitor/daemon.log` |

## CLI Commands

```bash
pause-monitor daemon      # Run sampler (foreground)
pause-monitor tui         # Interactive dashboard
pause-monitor status      # One-line health check
pause-monitor events      # List pause events
pause-monitor events <id> # Inspect specific event
pause-monitor history     # Query historical data
pause-monitor config      # Manage configuration
pause-monitor prune       # Delete old data per retention policy
pause-monitor install     # Set up launchd service
pause-monitor uninstall   # Remove launchd service
```

## Testing

```bash
uv run pytest                          # All tests
uv run pytest tests/test_stress.py     # Specific module
uv run pytest -v                       # Verbose
```

## Design Document

Full design rationale: `docs/plans/2026-01-20-pause-monitor-design.md`
