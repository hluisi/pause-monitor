# pause-monitor

A system health monitoring daemon for macOS that tracks down intermittent system pauses.

## The Problem

Your Mac occasionally freezes for 10-90 seconds. When it recovers, Activity Monitor shows nothing unusual. You have no idea what caused it.

## The Solution

`pause-monitor` runs in the background and:

1. **Detects pauses** using precise timing - if the system was unresponsive, it knows
2. **Captures forensics** immediately after recovery - process snapshots, spindump, system logs
3. **Tracks history** so you can spot patterns over days/weeks
4. **Uses smart stress detection** - not "high CPU" (meaningless), but actual contention signals

## Installation

```bash
# Install with uv
uv tool install pause-monitor

# Or from source
git clone https://github.com/hluisi/pause-monitor
cd pause-monitor
uv tool install .
```

## Usage

```bash
# Start the daemon (or use launchd - see below)
pause-monitor daemon

# Quick status check
pause-monitor status
# ✓ Healthy | Stress: 12% | Load: 3.4/16 | Mem: 73% | Last pause: 2h ago

# Launch the TUI dashboard
pause-monitor tui

# List recent pause events
pause-monitor events

# Inspect a specific event
pause-monitor events 3
```

## TUI Dashboard

```
┌─ pause-monitor ──────────────────────────────────────────── 09:32:15 ─┐
│                                                                        │
│  SYSTEM HEALTH          STRESS: ██░░░░░░░░ 12%        Mode: Normal 5s │
│  ───────────────────────────────────────────────────────────────────── │
│  CPU:  ████████░░░░░░░░ 47%    Load: 3.4/16 cores                     │
│  Mem:  ████████████░░░░ 73%    Free: 34 GB                            │
│  I/O:  ██░░░░░░░░░░░░░░  8%    R: 12 MB/s  W: 45 MB/s                 │
│                                                                        │
│  RECENT EVENTS                                                         │
│  ───────────────────────────────────────────────────────────────────── │
│  ⚠ 09:10:13  PAUSE 74.3s  [biomesyncd suspected]         [View: Enter]│
│                                                                        │
│  [q] Quit  [e] Events  [p] Processes  [h] History  [?] Help           │
└────────────────────────────────────────────────────────────────────────┘
```

## Running as a Service

```bash
# Install launchd service (starts on login)
pause-monitor install

# Remove service
pause-monitor uninstall
```

## How It Works

### Smart Stress Detection

High CPU doesn't mean problems. A process using 200% on a 16-core machine is fine.

Instead, `pause-monitor` calculates a **stress score** from actual contention signals:

| Signal | What it means |
|--------|---------------|
| Load/cores ratio | Processes are queuing for CPU |
| I/O wait | Processes blocked on disk |
| Memory pressure | System is compressing/swapping |
| Self-latency | Our own sleep took too long |

### Adaptive Sampling

- **Normal:** 5 second intervals (minimal overhead)
- **Elevated:** 1 second intervals when stress is building
- **Critical:** Preemptive capture before a potential freeze

### Pause Detection

Uses `time.monotonic()` to detect actual unresponsiveness. If a 5-second sleep takes 79 seconds, the system was frozen for 74 seconds.

## Configuration

```toml
# ~/.config/pause-monitor/config.toml

[sampling]
normal_interval = 5
elevated_interval = 1

[suspects]
patterns = ["codemeter", "bitdefender", "biomesyncd"]
```

## Data Storage

- **Config:** `~/.config/pause-monitor/config.toml`
- **Database:** `~/.local/share/pause-monitor/data.db`
- **Events:** `~/.local/share/pause-monitor/events/`

Data is automatically pruned after 30 days. Event forensics kept for 90 days.

## Requirements

- macOS (uses macOS-specific APIs for thermals, spindump, etc.)
- Python 3.11+

## License

MIT
