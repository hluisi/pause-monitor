# Design Specification

**Last updated:** 2026-01-22

## Source Documents

| Document | Date Processed | Status |
|----------|----------------|--------|
| docs/plans/2026-01-20-pause-monitor-design.md | 2026-01-21 | Archived |
| docs/plans/2026-01-21-ring-buffer-sentinel-design.md | 2026-01-22 | Archived |

## Overview

A **real-time** system health monitoring tool for macOS that tracks down intermittent system pauses. Uses multi-factor stress detection to distinguish "busy but fine" from "system degraded." Primary interface is a live TUI dashboard.

**Goals:**
1. Root cause identification - Capture enough data during/after pauses to identify the culprit process
2. Historical trending - Track system behavior over days/weeks to spot patterns
3. Real-time alerting - Know when the system is under stress before it freezes

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        pause-monitor                             │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │   Sampler    │───▶│   Storage    │◀───│     CLI      │       │
│  │   (daemon)   │    │   (SQLite)   │    │   Queries    │       │
│  └──────────────┘    └──────────────┘    └──────────────┘       │
│         │                   ▲                                    │
│         │                   │                                    │
│         ▼                   │                                    │
│  ┌──────────────┐    ┌──────────────┐                           │
│  │   Forensics  │    │     TUI      │                           │
│  │  (on pause)  │    │  Dashboard   │                           │
│  └──────────────┘    └──────────────┘                           │
└─────────────────────────────────────────────────────────────────┘
```

## Components

### Sampler (daemon.py)
- **Purpose:** Background daemon orchestrating sampling, detection, and forensics with Sentinel integration
- **Responsibilities:** Stream powermetrics, run Sentinel for fast pause detection, manage ring buffer, trigger forensics
- **Interfaces:** Reads config, writes to SQLite, triggers forensics, integrates with Sentinel
- **Key features:** 
  - Sentinel runs 100ms fast loop for pause detection
  - Ring buffer captures 30s of context before pauses
  - Tier-based monitoring (SENTINEL→ELEVATED→CRITICAL)
  - Automatic culprit identification from ring buffer

### Sentinel (sentinel.py)
- **Purpose:** Fast-loop pause detection with tiered monitoring
- **Responsibilities:** Run 100ms loop for pause detection, manage tier transitions, push samples to ring buffer
- **Components:**
  - `TierManager` - State machine for SENTINEL/ELEVATED/CRITICAL transitions
  - `Sentinel` - Main class with fast/slow loops and callbacks
- **Configuration:** `[sentinel]` and `[tiers]` config sections

### Ring Buffer (ringbuffer.py)
- **Purpose:** In-memory circular buffer for capturing pre-pause context
- **Responsibilities:** Store last N seconds of stress samples, capture process snapshots on tier transitions
- **Features:**
  - Thread-safe circular buffer
  - `freeze()` creates immutable snapshot for analysis
  - Process snapshots capture top 10 by CPU and memory

### Storage (storage.py)
- **Purpose:** SQLite database with WAL mode for concurrent access
- **Responsibilities:** Store samples, process snapshots, events, daemon state
- **Interfaces:** Used by daemon (write), TUI (read-only), CLI (read)
- **Location:** `~/.local/share/pause-monitor/data.db`

### Collector (collector.py)
- **Purpose:** Stream and parse powermetrics plist output
- **Responsibilities:** Run powermetrics subprocess, parse NUL-separated plists, extract metrics
- **Key features:** Handles malformed plists, text headers, streaming output

### Forensics (forensics.py)
- **Purpose:** Capture diagnostic data on pause detection
- **Responsibilities:** Process snapshot, spindump, tailspin save, log extraction
- **Triggers:** When interval > 2x expected (pause detected)
- **Output:** Event directories in `~/.local/share/pause-monitor/events/`

### Stress Calculator (stress.py)
- **Purpose:** Multi-factor stress scoring
- **Responsibilities:** Calculate stress breakdown from metrics, identify culprits
- **Output:** StressBreakdown dataclass with load, memory, thermal, latency, io scores

### TUI Dashboard (tui/)
- **Purpose:** Live dashboard showing current stats, recent events, trends
- **Framework:** Textual
- **Views:** Dashboard (default), Processes, Events, History
- **Refresh:** 1 second polling via aiosqlite read-only connection

### CLI (cli.py)
- **Purpose:** Command-line interface
- **Framework:** Click

### Notifications (notifications.py)
- **Purpose:** macOS notification center alerts
- **Methods:** terminal-notifier (preferred) or osascript fallback

### Sleep/Wake Detection (sleepwake.py)
- **Purpose:** Distinguish sleep from actual pauses
- **Methods:** pmset log parsing, clock drift detection

## Data Models

### samples table
| Field | Type | Purpose |
|-------|------|---------|
| id | INTEGER PRIMARY KEY | Auto-increment ID |
| timestamp | REAL | Unix timestamp with ms precision |
| interval | REAL | Actual seconds since last sample |
| cpu_pct | REAL | System-wide CPU % |
| load_avg | REAL | 1-minute load average |
| mem_available | INTEGER | Bytes available |
| swap_used | INTEGER | Bytes in swap |
| io_read | INTEGER | Bytes/sec read |
| io_write | INTEGER | Bytes/sec write |
| net_sent | INTEGER | Bytes/sec sent |
| net_recv | INTEGER | Bytes/sec received |
| cpu_temp | REAL | Celsius (privileged) |
| cpu_freq | INTEGER | MHz (privileged) |
| throttled | BOOLEAN | Thermal throttling active |
| gpu_pct | REAL | GPU utilization % |
| stress_total | INTEGER | Combined stress score 0-100 |
| stress_load | INTEGER | Load contribution 0-40 |
| stress_memory | INTEGER | Memory contribution 0-30 |
| stress_thermal | INTEGER | Thermal contribution 0-20 |
| stress_latency | INTEGER | Latency contribution 0-30 |
| stress_io | INTEGER | I/O contribution 0-20 |

### process_samples table
| Field | Type | Purpose |
|-------|------|---------|
| id | INTEGER PRIMARY KEY | Auto-increment ID |
| sample_id | INTEGER FK | References samples(id) |
| pid | INTEGER | Process ID |
| name | TEXT | Process name |
| cpu_pct | REAL | Process CPU % |
| mem_pct | REAL | Process memory % |
| io_read | INTEGER | Bytes/sec (via powermetrics) |
| io_write | INTEGER | Bytes/sec (via powermetrics) |
| energy_impact | REAL | Apple's composite energy score |
| is_suspect | BOOLEAN | Matches suspect list |

### events table
| Field | Type | Purpose |
|-------|------|---------|
| id | INTEGER PRIMARY KEY | Auto-increment ID |
| timestamp | REAL | Unix timestamp |
| duration | REAL | Seconds system was unresponsive |
| stress_* | INTEGER | Stress breakdown before pause |
| culprits | TEXT | JSON array of {pid, name, reason} |
| event_dir | TEXT | Path to forensics directory |
| notes | TEXT | User-added notes |

### daemon_state table
| Field | Type | Purpose |
|-------|------|---------|
| key | TEXT PRIMARY KEY | State key |
| value | TEXT | JSON-encoded value |
| updated_at | REAL | Unix timestamp |

Keys: `schema_version`, `io_baseline`, `last_sample_id`

## Configuration

Location: `~/.config/pause-monitor/config.toml`

| Section | Option | Default | Purpose |
|---------|--------|---------|---------|
| (root) | learning_mode | false | Collect data without alerts during calibration |
| sampling | normal_interval | 5 | Seconds between samples (normal mode) |
| sampling | elevated_interval | 1 | Seconds between samples (elevated mode) |
| sampling | elevation_threshold | 30 | Stress score to enter elevated mode |
| sampling | critical_threshold | 60 | Stress score for preemptive snapshot |
| **sentinel** | **fast_interval_ms** | **100** | **Fast loop interval (ms)** |
| **sentinel** | **ring_buffer_seconds** | **30** | **Ring buffer history size** |
| **tiers** | **elevated_threshold** | **15** | **Enter ELEVATED tier** |
| **tiers** | **critical_threshold** | **50** | **Enter CRITICAL tier** |
| retention | samples_days | 30 | Days to keep samples |
| retention | events_days | 90 | Days to keep events |
| alerts | enabled | true | Master switch for alerts |
| alerts | pause_detected | true | Alert on pause events |
| alerts | pause_min_duration | 2.0 | Minimum pause duration to alert |
| alerts | critical_stress | true | Alert on sustained critical stress |
| alerts | critical_threshold | 60 | Stress level for critical alert |
| alerts | critical_duration | 30 | Seconds stress must be sustained |
| alerts | elevated_entered | false | Alert when entering elevated mode |
| alerts | forensics_completed | true | Alert when forensics capture finishes |
| alerts | sound | true | Play sound with notifications |
| suspects | patterns | [...] | Process name patterns to flag |

## CLI Commands

| Command | Purpose |
|---------|---------|
| `pause-monitor daemon` | Run background sampler (foreground) |
| `pause-monitor tui` | Launch interactive dashboard |
| `pause-monitor status` | Quick health check (one-liner) |
| `pause-monitor events` | List pause events (supports --status filter) |
| `pause-monitor events <id>` | Inspect specific event |
| `pause-monitor events mark <id> <status>` | Change event status (reviewed/pinned/dismissed) |
| `pause-monitor history` | Query historical data |
| `pause-monitor history --at "<time>"` | Show what was happening at a specific time |
| `pause-monitor config` | Manage configuration |
| `pause-monitor prune` | Manual data cleanup |
| `pause-monitor install` | Set up launchd service, sudoers, tailspin |
| `pause-monitor uninstall` | Remove launchd service |
| `pause-monitor calibrate` | Show suggested thresholds from learning mode |

## Workflows

### Sampling Loop
1. Run powermetrics at 1s intervals (streaming)
2. Parse each plist sample
3. Calculate stress score
4. Check if elevated mode should change (hysteresis: elevate at 30, de-elevate at 20)
5. Store sample if: elevated mode OR every 5th sample
6. If pause detected (interval > 2x expected), trigger forensics
7. If critical stress (>60), trigger preemptive snapshot

### Pause Detection
1. Compare actual interval vs expected interval
2. If ratio > 2.0, check if system recently woke from sleep
3. If NOT sleep: record as pause event, trigger forensics
4. If sleep: log as sleep event, don't record as pause

### Forensics Capture (on pause)
1. Immediate process snapshot via psutil (~50ms)
2. Save tailspin trace (~1s, privileged)
3. Disk I/O snapshot via powermetrics (~1s, privileged)
4. Trigger spindump (~5-10s, privileged)
5. Thermal snapshot via powermetrics (~1s, privileged)
6. Extract system logs (~1s)
7. Write summary.json

### Install Process
1. Verify not running as root
2. Check prerequisites (macOS 12+, admin group)
3. Create data directories
4. Initialize database
5. Install sudoers rules (validates with visudo)
6. Enable tailspin
7. Install launchd plist
8. Validate forensics health

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Stress scoring over CPU thresholds | High CPU alone doesn't indicate problems; what matters is contention |
| Always 1s sampling, variable storage | Avoids restarting powermetrics when switching modes; stress calculation still happens every second |
| Hysteresis for mode transitions | Elevate at 30, de-elevate at 20 to prevent rapid mode cycling |
| WAL mode for SQLite | Allows concurrent daemon writes and TUI reads |
| tailspin for kernel traces | Only way to see kernel-level activity during freezes |
| pmset for sleep detection | No pyobjc dependency, works reliably, provides wake type |
| caffeinate over NSProcessInfo | No pyobjc required, simple subprocess approach |
| terminal-notifier over osascript | Better UX when available, osascript fallback always works |
| Privileged mode required | Per-process I/O is essential for identifying culprits; not optional |
| User-specific sudoers rules | Constrain output paths to prevent cross-user attacks |

## Privileged Operations

Required for full functionality (via sudoers):

| Operation | Command | Purpose |
|-----------|---------|---------|
| Per-process I/O | `powermetrics --show-process-io` | Identify disk I/O culprits |
| Kernel traces | `tailspin save` | Capture activity during freeze |
| Thread stacks | `spindump` | Post-pause forensics |
| Thermal data | `powermetrics --samplers thermal` | Detect throttling |

## Data Locations

| Purpose | Path |
|---------|------|
| Config | `~/.config/pause-monitor/config.toml` |
| Database | `~/.local/share/pause-monitor/data.db` |
| Events | `~/.local/share/pause-monitor/events/` |
| Daemon log | `~/.local/share/pause-monitor/daemon.log` |
| PID file | `~/.local/share/pause-monitor/daemon.pid` |
| launchd plist | `~/Library/LaunchAgents/com.local.pause-monitor.plist` |
| sudoers | `/etc/sudoers.d/pause-monitor-<username>` |

## Dependencies

| Package | Purpose |
|---------|---------|
| textual | Modern TUI framework |
| rich | Pretty CLI output |
| click | CLI framework |
| aiosqlite | Async SQLite for TUI |
| structlog | Structured daemon logging |
| tomlkit | TOML config with comment preservation |

Not required: psutil (powermetrics provides all metrics), pyobjc (pmset + clock drift for sleep detection)
