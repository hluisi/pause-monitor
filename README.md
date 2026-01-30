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

---

## Quick Start

### Step 1: Install

**Prerequisites:** You need [uv](https://docs.astral.sh/uv/) (Python package manager). If you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Install pause-monitor:**

```bash
# From source (recommended)
git clone https://github.com/hluisi/pause-monitor
cd pause-monitor
uv tool install .
```

### Step 2: Run It

```bash
# Start the daemon (runs in foreground, Ctrl+C to stop)
pause-monitor daemon
```

That's it! The daemon is now monitoring your system.

### Step 3: Set Up Auto-Start (Optional)

To have pause-monitor start automatically when you log in:

```bash
pause-monitor install
```

To remove auto-start later:

```bash
pause-monitor uninstall
```

---

## Commands

### Check System Status

```bash
pause-monitor status
```

Shows a one-line health summary:
```
Healthy | Stress: 12% | Load: 3.4/16 | Mem: 73% | Last pause: 2h ago
```

### Interactive Dashboard

```bash
pause-monitor tui
```

Opens a live-updating dashboard showing CPU, memory, I/O, and recent events:

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

Press `q` to quit.

### View Pause Events

```bash
# List all pause events
pause-monitor events

# View details of a specific event (e.g., event #3)
pause-monitor events 3
```

### View Historical Data

```bash
# Show samples from the last hour (default)
pause-monitor history

# Show samples from the last 24 hours
pause-monitor history -H 24

# Show only high-stress periods
pause-monitor history --high-stress

# Export as JSON or CSV
pause-monitor history --format json
pause-monitor history --format csv
```

### Manage Configuration

```bash
# Show current settings
pause-monitor config show

# Edit configuration file
pause-monitor config edit

# Reset to defaults
pause-monitor config reset
```

### Clean Up Old Data

```bash
# Preview what would be deleted
pause-monitor prune --dry-run

# Delete old data (confirms before deleting)
pause-monitor prune

# Delete without confirmation
pause-monitor prune --yes
```

---

## Configuration

Configuration is stored at `~/.config/pause-monitor/config.toml`. Edit it with:

```bash
pause-monitor config edit
```

### Example Configuration

```toml
[sampling]
normal_interval = 5      # Seconds between samples (normal mode)
elevated_interval = 1    # Seconds between samples (elevated stress)
elevation_threshold = 30 # Stress % to trigger elevated mode
critical_threshold = 60  # Stress % to trigger preemptive capture

[retention]
samples_days = 30        # Keep sample data for 30 days
events_days = 90         # Keep pause event forensics for 90 days

[alerts]
enabled = true           # Show macOS notifications on pause detection
sound = false            # Play sound with notifications

[suspects]
# Process name patterns that commonly cause pauses
patterns = ["codemeter", "bitdefender", "biomesyncd"]
```

---

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

- **Normal (stress < 30%):** 5 second intervals (minimal overhead)
- **Elevated (stress 30-60%):** 1 second intervals when stress is building
- **Critical (stress > 60%):** Preemptive capture before a potential freeze

### Pause Detection

Uses `time.monotonic()` to detect actual unresponsiveness. If a 5-second sleep takes 79 seconds, the system was frozen for 74 seconds.

When a pause is detected, pause-monitor immediately captures:
- Full process snapshot
- `spindump` (thread stacks)
- `tailspin` (kernel traces, if available)
- Filtered system logs from the pause window

---

## Data Storage

| Purpose | Location |
|---------|----------|
| Config | `~/.config/pause-monitor/config.toml` |
| Database | `~/.local/share/pause-monitor/data.db` |
| Event forensics | `~/.local/share/pause-monitor/events/` |
| Daemon logs | `~/.local/share/pause-monitor/daemon.log` |

Data is automatically pruned: samples after 30 days, event forensics after 90 days.

---

## Running as a Service

### Install (User)

Runs when you log in:

```bash
pause-monitor install
```

### Install (System-wide)

Runs at boot, even before login (requires admin):

```bash
sudo pause-monitor install --system
```

### Check Service Status

```bash
# User service
launchctl list | grep pause-monitor

# System service
sudo launchctl list | grep pause-monitor
```

### View Logs

```bash
# Daemon output
tail -f ~/.local/share/pause-monitor/daemon.log

# launchd stdout/stderr
tail -f ~/Library/Logs/pause-monitor.log
```

### Uninstall

```bash
# User service
pause-monitor uninstall

# System service
sudo pause-monitor uninstall --system

# Also delete all data
pause-monitor uninstall --force
```

---

## Development

### Setup

```bash
git clone https://github.com/hluisi/pause-monitor
cd pause-monitor
uv sync                  # Install dependencies
```

### Run Tests

```bash
uv run pytest            # Run all tests
uv run pytest -v         # Verbose output
uv run pytest -k stress  # Run tests matching "stress"
```

### Lint and Format

```bash
uv run ruff check .      # Check for issues
uv run ruff format .     # Auto-format code
```

### Run from Source

```bash
uv run pause-monitor daemon
uv run pause-monitor status
uv run pause-monitor tui
```

---

## Troubleshooting

### "Permission denied" errors

Forensic capture requires a sudoers rule for tailspin. Run the install command to set it up:

```bash
sudo pause-monitor install
```

This creates `/etc/sudoers.d/pause-monitor` with a narrow rule allowing tailspin captures to `/tmp/pause-monitor/`. The daemon itself runs unprivileged.

### Daemon won't start

Check if it's already running:

```bash
pause-monitor status
```

If it says "already running", stop the existing instance:

```bash
# If running as a service
pause-monitor uninstall
pause-monitor install

# If stuck, find and kill the process
ps aux | grep pause-monitor
kill <pid>
```

### No data in history/events

The daemon needs to run for a while to collect data. Check:

```bash
# Is the daemon running?
pause-monitor status

# Is the database being created?
ls -la ~/.local/share/pause-monitor/
```

### High CPU usage from pause-monitor itself

This shouldn't happen. If it does:

1. Check your config: `pause-monitor config show`
2. Reset to defaults: `pause-monitor config reset`
3. File an issue with your system details

---

## Requirements

- **macOS** (uses macOS-specific APIs for thermals, spindump, etc.)
- **Python 3.11+**
- **uv** (for installation)

---

## License

MIT
