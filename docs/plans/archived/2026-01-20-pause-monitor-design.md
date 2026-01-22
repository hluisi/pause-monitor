# pause-monitor Design Document

**Date:** 2026-01-20
**Status:** Approved
**Author:** Hunter (with Claude)

## Overview

A **real-time** system health monitoring tool for macOS that tracks down intermittent system pauses. This is not a post-hoc analysis tool—it's designed for active monitoring of an ongoing problem, with a live TUI dashboard as the primary interface.

Unlike simple CPU monitors, it uses multi-factor stress detection to distinguish "busy but fine" from "system degraded." The TUI provides immediate visibility into system state, letting you watch stress factors in real-time and catch the buildup to a pause as it happens.

## Goals

1. **Root cause identification** - Capture enough data during/after pauses to identify the culprit process
2. **Historical trending** - Track system behavior over days/weeks to spot patterns
3. **Real-time alerting** - Know when the system is under stress before it freezes

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        pause-monitor                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
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
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Components

| Component | Responsibility |
|-----------|----------------|
| **Sampler** | Background daemon running a streaming `powermetrics` subprocess. Collects comprehensive metrics every 5s (or 1s when elevated) including per-process I/O, thermal state, GPU utilization, and energy impact. |
| **Storage** | SQLite database at `~/.local/share/pause-monitor/data.db`. Auto-prunes data older than 30 days. |
| **Forensics** | Triggered when a pause >2s is detected. Captures full process list, runs `spindump`, extracts system logs. |
| **CLI** | Commands like `pause-monitor status`, `pause-monitor history`, `pause-monitor events`. |
| **TUI** | Live dashboard showing current stats, recent events, trends. Built with `textual`. |

### Streaming powermetrics Architecture

The daemon runs `powermetrics` as a long-lived subprocess in streaming mode, parsing plist output as it arrives. This is more efficient than spawning a new process for each sample and provides the richest system data available on macOS.

**Command:**
```bash
sudo powermetrics -i 5000 \
  --samplers cpu_power,gpu_power,thermal,tasks,ane_power,disk \
  --show-process-io --show-process-gpu --show-process-coalition \
  --show-responsible-pid --show-process-energy --show-process-samp-norm \
  -f plist
```

**Streaming Architecture:**
```
┌─────────────────────────────────────────────────────────────┐
│                      pause-monitor daemon                    │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐     ┌──────────────┐     ┌─────────────┐  │
│  │ powermetrics │────▶│    Parser    │────▶│   Storage   │  │
│  │  subprocess  │     │  (plistlib)  │     │  (SQLite)   │  │
│  │   (stdout)   │     │              │     │             │  │
│  └──────────────┘     └──────────────┘     └─────────────┘  │
│         │                    │                    │          │
│         │                    ▼                    │          │
│         │             ┌──────────────┐            │          │
│         │             │    Stress    │            │          │
│         │             │  Calculator  │            │          │
│         │             └──────────────┘            │          │
│         │                    │                    │          │
│         ▼                    ▼                    │          │
│  ┌──────────────┐     ┌──────────────┐            │          │
│  │   Interval   │     │  Forensics   │            │          │
│  │   Manager    │     │   Trigger    │────────────┘          │
│  │ (5s ↔ 1s)   │     │              │                       │
│  └──────────────┘     └──────────────┘                       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**Interval Strategy (Always 1s, Variable Storage):**

Rather than restarting powermetrics when switching between normal and elevated modes (which adds subprocess overhead to an already stressed system), we always run at 1-second intervals and control what gets stored:

- **Normal mode:** Store every 5th sample (effective 5s resolution), discard intermediate samples after stress calculation
- **Elevated mode (stress > 30):** Store every sample (1s resolution)
- **Mode transitions:** Instant, no subprocess restart needed

```python
class SamplePolicy:
    def __init__(self):
        self.sample_count = 0
        self.elevated = False

    def should_store(self, stress_score: int) -> bool:
        self.sample_count += 1

        # Check for mode transition
        if stress_score > 30 and not self.elevated:
            self.elevated = True
            log.info("Entering elevated mode", stress=stress_score)
        elif stress_score < 20 and self.elevated:  # Hysteresis: de-elevate at 20, not 30
            self.elevated = False
            log.info("Exiting elevated mode", stress=stress_score)

        if self.elevated:
            return True  # Store every sample
        else:
            return self.sample_count % 5 == 0  # Store every 5th sample
```

**Benefits:**
- No monitoring gap during mode transition
- No additional subprocess overhead when system is already stressed
- Stress calculation still happens every second (catches fast buildups)
- Hysteresis prevents rapid mode cycling (elevate at 30, de-elevate at 20)

**Plist Parsing:**

powermetrics outputs NUL-separated plists when streaming. Each plist contains a complete snapshot. The parser must handle:
- First sample may have a text header before the plist
- Malformed plists (hardware glitches, process termination)
- Partial reads that split multi-byte sequences

```python
import plistlib
import subprocess
import structlog

log = structlog.get_logger()

# Plist documents start with XML declaration or DOCTYPE
PLIST_MARKERS = (b'<?xml', b'<!DOCTYPE', b'<plist')

def stream_powermetrics(interval_ms: int):
    """Stream samples from powermetrics, yielding parsed plist dicts.

    Handles edge cases:
    - Skips any text before the first plist (headers, warnings)
    - Recovers from malformed plists by skipping to next NUL boundary
    - Logs errors but doesn't crash the daemon
    """
    proc = subprocess.Popen(
        ['sudo', 'powermetrics', '-i', str(interval_ms),
         '--samplers', 'cpu_power,gpu_power,thermal,tasks,ane_power,disk',
         '--show-process-io', '--show-process-gpu', '--show-process-coalition',
         '--show-responsible-pid', '--show-process-energy', '--show-process-samp-norm',
         '--handle-invalid-values',  # Output invalid=true instead of aborting
         '-f', 'plist'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,  # Capture stderr separately for logging
    )

    buffer = b''
    samples_parsed = 0
    consecutive_errors = 0

    for chunk in iter(lambda: proc.stdout.read(4096), b''):
        buffer += chunk

        while b'\x00' in buffer:
            plist_data, buffer = buffer.split(b'\x00', 1)

            if not plist_data:
                continue

            # Find start of actual plist (skip any header text)
            plist_start = -1
            for marker in PLIST_MARKERS:
                idx = plist_data.find(marker)
                if idx != -1 and (plist_start == -1 or idx < plist_start):
                    plist_start = idx

            if plist_start == -1:
                # No plist found in this segment, skip it
                if samples_parsed == 0:
                    log.debug("powermetrics_header_skipped", bytes=len(plist_data))
                continue

            if plist_start > 0:
                # Log and skip the header portion
                header = plist_data[:plist_start]
                log.debug("powermetrics_header", header=header.decode('utf-8', errors='replace'))
                plist_data = plist_data[plist_start:]

            try:
                data = plistlib.loads(plist_data)
                samples_parsed += 1
                consecutive_errors = 0
                yield data

            except plistlib.InvalidFileException as e:
                consecutive_errors += 1
                log.warning(
                    "powermetrics_malformed_plist",
                    error=str(e),
                    bytes=len(plist_data),
                    consecutive_errors=consecutive_errors,
                )

                # If we get many consecutive errors, something is wrong
                if consecutive_errors >= 5:
                    log.error("powermetrics_repeated_parse_failures", count=consecutive_errors)
                    # Could restart powermetrics here, but let the outer loop handle it

    # Process ended - check why
    returncode = proc.wait()
    if returncode != 0:
        stderr = proc.stderr.read().decode('utf-8', errors='replace')
        log.error("powermetrics_exited", returncode=returncode, stderr=stderr[:500])
```

**Robustness features:**
- `--handle-invalid-values` flag tells powermetrics to output `invalid=true` for hardware read errors instead of aborting
- Detects and skips text headers before first plist
- Logs and continues on malformed plists (doesn't crash daemon)
- Tracks consecutive errors to detect systemic problems
- Captures stderr separately for diagnostics

**Key plist fields extracted:**

| Field Path | Metric |
|------------|--------|
| `processor/clusters/*/cpu_power` | Per-cluster CPU power (P-cores vs E-cores) |
| `thermal_pressure` | System thermal state |
| `gpu/busy_ratio` | GPU utilization |
| `tasks[*]/name`, `tasks[*]/pid` | Process identification |
| `tasks[*]/cputime_sample_ms_per_s` | CPU usage (normalized to sample window) |
| `tasks[*]/disk_bytes_read_per_s` | Per-process disk read rate |
| `tasks[*]/disk_bytes_written_per_s` | Per-process disk write rate |
| `tasks[*]/energy_impact` | Apple's composite energy score |
| `tasks[*]/responsible_pid` | Parent app for XPC helpers |
| `tasks[*]/coalition_id` | Process coalition grouping |

### Daemon Lifecycle Management

The daemon requires careful lifecycle handling:

**PID File:** `~/.local/share/pause-monitor/daemon.pid`
- Created on startup, removed on clean shutdown
- Used by `pause-monitor status` to check if daemon is running
- Prevents multiple daemon instances

**Signal Handling:**
```python
signal.signal(signal.SIGTERM, graceful_shutdown)  # launchd stop
signal.signal(signal.SIGINT, graceful_shutdown)   # Ctrl+C
signal.signal(signal.SIGHUP, reload_config)       # Config reload
```

**Graceful Shutdown:**
1. Terminate powermetrics subprocess (SIGTERM, then SIGKILL if needed)
2. Flush pending database writes
3. Remove PID file
4. Exit with code 0

**Health Check:** `pause-monitor status` verifies:
1. PID file exists and process is running
2. Database is accessible and not corrupted
3. Last sample timestamp is recent (< 2x sampling interval)

**Crash Recovery:**
- Stale PID files (process dead) are removed on next startup
- Partial database writes protected by SQLite WAL mode
- Forensics captures are atomic (write to temp, then rename)

**Error Handling:**

| Error | Detection | Recovery |
|-------|-----------|----------|
| **Malformed plist from powermetrics** | `plistlib.loads()` raises `InvalidFileException` | Log error, skip sample, continue streaming |
| **powermetrics crashes/exits** | Subprocess returns non-zero or EOF on stdout | Log error, restart subprocess with backoff (1s, 2s, 4s, max 30s) |
| **powermetrics hangs** | No output for 3x expected interval | SIGTERM, wait 5s, SIGKILL if needed, restart |
| **SQLite database corruption** | `sqlite3.DatabaseError` on any operation | Log error, attempt `PRAGMA integrity_check`, if fails: backup corrupt DB, create fresh |
| **Disk full during forensics** | `OSError` with `ENOSPC` | Skip forensics capture, log warning, continue monitoring |
| **sudo command timeout** | subprocess timeout (30s default) | Kill subprocess, log error, skip this forensic artifact |
| **tailspin not enabled** | `tailspin save` returns error or empty trace | Log warning on startup, skip tailspin capture in forensics |
| **Memory exhaustion** | `MemoryError` during large operation | Let process crash (daemon will restart via launchd) |

**Principle:** The daemon should never crash from expected errors (bad data, missing permissions). It should crash on unexpected errors (memory exhaustion, logic bugs) to surface issues early.

## Data Model

### SQLite Tables

```sql
-- Periodic samples (one row per sample interval)
samples (
    id          INTEGER PRIMARY KEY,
    timestamp   REAL,           -- Unix timestamp with ms precision
    interval    REAL,           -- Actual seconds since last sample (detects pauses)

    -- Raw metrics
    cpu_pct     REAL,           -- System-wide CPU %
    load_avg    REAL,           -- 1-minute load average
    mem_available INTEGER,      -- Bytes available (not "free")
    swap_used   INTEGER,        -- Bytes in swap
    io_read     INTEGER,        -- Bytes/sec read
    io_write    INTEGER,        -- Bytes/sec write
    net_sent    INTEGER,        -- Bytes/sec sent
    net_recv    INTEGER,        -- Bytes/sec received
    cpu_temp    REAL,           -- Celsius (null if unprivileged)
    cpu_freq    INTEGER,        -- MHz (null if unprivileged)
    throttled   BOOLEAN,        -- Thermal throttling active (null if unprivileged)
    gpu_pct     REAL,           -- GPU utilization % (null if unprivileged)

    -- Stress breakdown (for historical analysis)
    stress_total   INTEGER,     -- Combined stress score 0-100
    stress_load    INTEGER,     -- Load contribution 0-40
    stress_memory  INTEGER,     -- Memory contribution 0-30
    stress_thermal INTEGER,     -- Thermal contribution 0-20
    stress_latency INTEGER,     -- Latency contribution 0-30
    stress_io      INTEGER      -- I/O contribution 0-20
)

-- Per-process snapshots (linked to samples, only for top consumers)
-- Top 10 by CPU + top 5 by I/O (if privileged) + any suspects
-- NOTE: psutil.Process.io_counters() is NOT available on macOS.
-- Per-process I/O requires privileged powermetrics (--show-process-io flag).
-- Without privileged mode, io_read/io_write will always be NULL.
process_samples (
    id          INTEGER PRIMARY KEY,
    sample_id   INTEGER REFERENCES samples(id),
    pid         INTEGER,
    name        TEXT,           -- Process name
    cpu_pct     REAL,
    mem_pct     REAL,
    io_read     INTEGER,        -- Bytes/sec (via powermetrics, null if unprivileged)
    io_write    INTEGER,        -- Bytes/sec (via powermetrics, null if unprivileged)
    is_suspect  BOOLEAN         -- Matches suspect list
)

-- Pause events (when interval > threshold)
events (
    id          INTEGER PRIMARY KEY,
    timestamp   REAL,
    duration    REAL,           -- Seconds system was unresponsive

    -- Stress state before pause (from last sample)
    stress_total   INTEGER,
    stress_load    INTEGER,
    stress_memory  INTEGER,
    stress_thermal INTEGER,
    stress_latency INTEGER,
    stress_io      INTEGER,

    -- Identified culprits (JSON array of {pid, name, reason})
    culprits    TEXT,           -- e.g., [{"pid": 123, "name": "mdworker", "reason": "I/O"}]

    -- Forensics paths
    event_dir   TEXT,           -- Path to event directory
    notes       TEXT            -- User-added notes (optional)
)

-- Daemon state (persisted across restarts)
daemon_state (
    key         TEXT PRIMARY KEY,
    value       TEXT,           -- JSON-encoded value
    updated_at  REAL            -- Unix timestamp
)
-- Keys:
--   io_baseline: exponential moving average of I/O rate (bytes/sec)
--   last_sample_id: for crash recovery
```

### Database Initialization

WAL mode must be enabled at database creation for concurrent daemon writes and TUI reads:

```python
def init_database(db_path: Path) -> None:
    """Initialize database with WAL mode and optimal pragmas."""
    conn = sqlite3.connect(db_path)
    try:
        # WAL mode: allows concurrent reads while daemon writes
        conn.execute("PRAGMA journal_mode=WAL")

        # Sync less aggressively (WAL provides crash safety)
        conn.execute("PRAGMA synchronous=NORMAL")

        # Limit WAL file size to 16MB before checkpoint
        conn.execute("PRAGMA journal_size_limit=16777216")

        # Enable foreign keys
        conn.execute("PRAGMA foreign_keys=ON")

        # Create tables...
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
```

**Concurrency model:**
- **Daemon:** Single writer, uses standard `sqlite3` connection
- **TUI:** Read-only via `aiosqlite.connect(path + "?mode=ro", uri=True)`
- **CLI queries:** Read-only, short-lived connections

WAL mode ensures TUI reads never block daemon writes. The TUI may see slightly stale data (up to 1 second old) but this is acceptable for real-time display.

### Storage Estimates (30 days)

- ~518K samples at 5s intervals = ~50 MB for `samples` table
- ~5 process rows per sample = ~250 MB for `process_samples`
- Events + forensics: ~5 MB per pause event
- **Total: ~300-500 MB typical**

### Auto-pruning

Daily job deletes samples older than 30 days, keeps event forensics for 90 days.

### Schema Versioning

The database schema will evolve as features are added. We use a simple version-based migration system:

```python
SCHEMA_VERSION = 1  # Increment when schema changes

def get_schema_version(conn: sqlite3.Connection) -> int:
    """Get current schema version from database."""
    try:
        row = conn.execute(
            "SELECT value FROM daemon_state WHERE key = 'schema_version'"
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0  # Table doesn't exist yet

def migrate_database(conn: sqlite3.Connection) -> None:
    """Apply any pending schema migrations."""
    current = get_schema_version(conn)

    if current == SCHEMA_VERSION:
        return  # Already up to date

    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {current} is newer than code version {SCHEMA_VERSION}. "
            "You may need to update pause-monitor."
        )

    log.info("schema_migration_starting", from_version=current, to_version=SCHEMA_VERSION)

    # Apply migrations in order
    migrations = {
        0: migrate_v0_to_v1,  # Initial schema creation
        # 1: migrate_v1_to_v2,  # Future migrations
    }

    for version in range(current, SCHEMA_VERSION):
        log.info("applying_migration", version=version + 1)
        migrations[version](conn)

    conn.execute(
        "INSERT OR REPLACE INTO daemon_state (key, value, updated_at) VALUES (?, ?, ?)",
        ('schema_version', str(SCHEMA_VERSION), time.time())
    )
    conn.commit()
    log.info("schema_migration_complete", version=SCHEMA_VERSION)


def migrate_v0_to_v1(conn: sqlite3.Connection) -> None:
    """Initial schema creation (version 0 -> 1)."""
    conn.executescript(SCHEMA)  # Create all tables
```

**Migration policy:**
- Migrations must be backward-compatible where possible (add columns with defaults, don't remove)
- Breaking migrations should backup data before applying
- Schema version is checked on every daemon startup

### Log Rotation

Daemon logs at `~/.local/share/pause-monitor/daemon.log` need rotation to prevent unbounded growth.

**Option 1: Internal rotation (preferred)**

The daemon rotates its own log file:

```python
import logging
from logging.handlers import RotatingFileHandler

def setup_logging(log_path: Path) -> None:
    """Configure logging with rotation."""
    handler = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=3,               # Keep 3 old logs
        encoding='utf-8',
    )
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s %(message)s'
    ))

    # Configure structlog to use this handler
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=handler.stream),
    )
```

**Option 2: newsyslog (macOS native)**

Add to `/etc/newsyslog.d/pause-monitor.conf`:

```
# logfilename                                      [owner:group] mode count size when  flags
/Users/hunter/.local/share/pause-monitor/daemon.log              644  3     10240 *     J
```

This rotates when log reaches 10MB, keeps 3 compressed backups.

**Note:** The launchd plist uses `StandardOutPath` which doesn't support rotation directly. The daemon must handle rotation internally or we must configure newsyslog.

## Stress Detection

**Key insight:** CPU percentage is meaningless. What matters is *contention* - are processes waiting for resources?

### Stress Signals

| Signal | How to measure | Why it matters |
|--------|----------------|----------------|
| **Load vs cores** | `load_avg / core_count` | >1.0 means processes are queuing |
| **Disk I/O saturation** | `powermetrics --show-process-io` | High disk activity blocks processes |
| **Memory pressure** | `memory_pressure` CLI or `vm_stat` compressor ratio | System is compressing/swapping |
| **Thermal throttling** | `powermetrics` (privileged) | CPU running below capacity |
| **Self-latency** | `actual_sleep - expected_sleep` | Direct measure of responsiveness |
| **Run queue depth** | `vm_stat` pageins/outs rate | Paging activity spike |

**Memory pressure detection on macOS:**

macOS provides several approaches for detecting memory pressure, from fastest to most detailed:

```python
import ctypes
import subprocess

def get_memory_pressure_fast() -> int:
    """Get memory pressure level via sysctl (fastest, no subprocess)."""
    # kern.memorystatus_level returns percentage of memory "free" (0-100)
    # Higher = more memory available = less pressure
    libc = ctypes.CDLL('/usr/lib/libc.dylib')
    size = ctypes.c_size_t(4)
    level = ctypes.c_int()
    libc.sysctlbyname(
        b'kern.memorystatus_level',
        ctypes.byref(level), ctypes.byref(size), None, 0
    )
    return level.value  # e.g., 93 means 93% free, 7% pressure

def get_memory_pressure_level() -> int:
    """Get memory pressure state via sysctl."""
    # kern.memorystatus_vm_pressure_level returns discrete levels:
    #   1 = Normal, 2 = Warning, 4 = Critical
    libc = ctypes.CDLL('/usr/lib/libc.dylib')
    size = ctypes.c_size_t(4)
    level = ctypes.c_int()
    libc.sysctlbyname(
        b'kern.memorystatus_vm_pressure_level',
        ctypes.byref(level), ctypes.byref(size), None, 0
    )
    return level.value

def get_compressor_ratio() -> float:
    """Get memory compressor efficiency via vm_stat (most detailed)."""
    # Pages stored in compressor / Pages occupied by compressor
    # Healthy: 15-20x ratio under light load, can exceed 100x when idle
    # Stressed: <5x ratio (compressor working hard, diminishing returns)
    result = subprocess.run(['vm_stat'], capture_output=True, text=True, timeout=5)
    # Parse "Pages stored in compressor" and "Pages occupied by compressor"
    stored = occupied = 0
    for line in result.stdout.splitlines():
        if 'Pages stored in compressor' in line:
            stored = int(line.split(':')[1].strip().rstrip('.'))
        elif 'Pages occupied by compressor' in line:
            occupied = int(line.split(':')[1].strip().rstrip('.'))
    return stored / occupied if occupied > 0 else float('inf')
```

**Recommended approach:** Use `kern.memorystatus_level` sysctl for fast, subprocess-free memory pressure detection. It returns a percentage (0-100) where higher values mean more memory is available. Categorize as:
- **Normal:** >50% available
- **Warning:** 20-50% available
- **Critical:** <20% available

Note: `psutil.cpu_times().iowait` is Linux-only. On macOS, we infer I/O pressure from disk activity rates and self-latency.

### Stress Score Calculation

Returns both total score and per-factor breakdown for TUI display:

```python
@dataclass
class StressBreakdown:
    load: int       # 0-40: load/cores ratio
    memory: int     # 0-30: memory pressure
    thermal: int    # 0-20: throttling active
    latency: int    # 0-30: self-latency
    io: int         # 0-20: disk I/O spike

    @property
    def total(self) -> int:
        return min(100, self.load + self.memory + self.thermal + self.latency + self.io)

def calculate_stress() -> StressBreakdown:
    # Load average relative to cores (max 40 points)
    load_ratio = load_avg_1min / core_count
    load_score = min(40, max(0, (load_ratio - 1.0) * 20))

    # Memory pressure (max 30 points)
    # Note: Use "available" memory, not "free"—macOS caches aggressively
    mem_score = min(30, max(0, (20 - mem_available_pct) * 1.5))

    # Thermal throttling (20 points if active, privileged only)
    thermal_score = 20 if throttled else 0

    # Self-latency (max 30 points)
    # NOTE: This is DETECTION, not PREDICTION. A latency spike means we already
    # experienced a mini-pause. We include it in stress because:
    # 1. System instability often comes in clusters (one pause predicts more)
    # 2. Elevated sampling catches the NEXT pause with more detail
    # 3. It surfaces issues invisible to other metrics (scheduler delays, etc.)
    latency_ratio = actual_interval / expected_interval
    latency_score = min(30, max(0, (latency_ratio - 1.0) * 20)) if latency_ratio > 1.5 else 0

    # Disk I/O spike detection (max 20 points, privileged only)
    #
    # io_rate = current bytes/sec (read + write)
    #
    # Two-tier baseline to catch both spikes and sustained high I/O:
    #   1. io_baseline_fast = EMA with alpha=0.1 (responds in ~10 samples)
    #   2. io_baseline_slow = EMA with alpha=0.001 (responds in ~1000 samples / ~17 min)
    #
    # Score triggers on EITHER:
    #   - Spike: io_rate > io_baseline_fast * 10 (sudden burst)
    #   - Sustained high: io_rate > 100 MB/s absolute threshold (catches prolonged indexing)
    #
    # The slow baseline is used for forensics attribution, not scoring—it shows what
    # "normal" looks like on this system over time.
    #
    # Default baselines on first run: 10 MB/s (realistic for modern SSDs)
    # Baselines are persisted in daemon_state table.

    spike_detected = io_rate > io_baseline_fast * 10
    sustained_high = io_rate > 100_000_000  # 100 MB/s absolute
    io_score = 20 if (spike_detected or sustained_high) else 0

    return StressBreakdown(
        load=int(load_score),
        memory=int(mem_score),
        thermal=thermal_score,
        latency=int(latency_score),
        io=io_score
    )
```

### Learning Mode and Baseline Calibration

The stress score uses heuristic weights that may need tuning for different hardware (MacBook Air vs Mac Studio) and workloads (development vs video editing). The daemon supports a **learning mode** for calibration.

**Learning Mode Behavior:**

When `learning_mode = true` in config:
1. Collect all metrics normally
2. Calculate stress scores but **don't trigger** elevated sampling or alerts
3. Store samples at 5s intervals regardless of stress level
4. Log stress breakdown for each sample to enable post-hoc analysis

```python
@dataclass
class LearningModeState:
    """Track statistics during learning mode for calibration."""
    samples_collected: int = 0
    stress_histogram: dict[int, int] = field(default_factory=dict)  # score -> count
    pause_stress_scores: list[int] = field(default_factory=list)  # stress at pause time

    def record_sample(self, stress: int) -> None:
        self.samples_collected += 1
        bucket = (stress // 10) * 10  # 0-9 -> 0, 10-19 -> 10, etc.
        self.stress_histogram[bucket] = self.stress_histogram.get(bucket, 0) + 1

    def record_pause(self, stress_before: int) -> None:
        self.pause_stress_scores.append(stress_before)

    def suggest_thresholds(self) -> dict[str, int]:
        """Analyze collected data to suggest optimal thresholds."""
        if not self.pause_stress_scores:
            return {"note": "No pauses recorded yet - keep learning mode active"}

        # Elevation threshold: catch 90% of pauses
        elevation = sorted(self.pause_stress_scores)[len(self.pause_stress_scores) // 10]

        # Critical threshold: top 10% of pause-preceding stress
        critical = sorted(self.pause_stress_scores)[-(len(self.pause_stress_scores) // 10 + 1)]

        return {
            "suggested_elevation_threshold": max(20, elevation - 10),
            "suggested_critical_threshold": max(50, critical - 5),
            "pauses_analyzed": len(self.pause_stress_scores),
            "samples_collected": self.samples_collected,
        }
```

**Learning Mode Duration:**

Recommend running learning mode for 1-2 weeks during normal use. The daemon logs a reminder after 7 days:

```
learning_mode active for 7 days, 3 pauses recorded. Run 'pause-monitor calibrate' to see suggested thresholds.
```

**I/O Baseline Learning Period:**

On first startup (or after database reset), I/O baselines need time to stabilize:

```python
class IOBaselineManager:
    """Manage I/O baseline with learning period awareness."""

    LEARNING_SAMPLES = 60  # ~1 minute at 1s sampling
    DEFAULT_BASELINE = 10_000_000  # 10 MB/s - conservative default for SSDs

    def __init__(self, persisted_baseline: float | None):
        self.baseline_fast = persisted_baseline or self.DEFAULT_BASELINE
        self.baseline_slow = persisted_baseline or self.DEFAULT_BASELINE
        self.samples_seen = 0 if persisted_baseline is None else self.LEARNING_SAMPLES
        self.learning = self.samples_seen < self.LEARNING_SAMPLES

    def update(self, io_rate: float) -> None:
        """Update baselines with new I/O rate observation."""
        self.samples_seen += 1

        # During learning period, use faster convergence
        if self.learning:
            alpha_fast = 0.3  # Converge quickly during learning
            alpha_slow = 0.1

            if self.samples_seen >= self.LEARNING_SAMPLES:
                self.learning = False
                log.info("io_baseline_learning_complete",
                        baseline_fast=self.baseline_fast,
                        baseline_slow=self.baseline_slow)
        else:
            alpha_fast = 0.1   # Normal operation
            alpha_slow = 0.001

        self.baseline_fast = alpha_fast * io_rate + (1 - alpha_fast) * self.baseline_fast
        self.baseline_slow = alpha_slow * io_rate + (1 - alpha_slow) * self.baseline_slow

    def is_spike(self, io_rate: float) -> bool:
        """Check if current I/O rate is a spike relative to baseline.

        During learning period, use absolute threshold only to avoid false positives.
        """
        if self.learning:
            # Only flag extreme absolute values during learning
            return io_rate > 200_000_000  # 200 MB/s

        return io_rate > self.baseline_fast * 10
```

### Culprit Identification

When stress is elevated, identify processes that correlate with active stress factors:

```python
def identify_culprits(breakdown: StressBreakdown, processes: list[Process]) -> list[Process]:
    culprits = []

    # If load is contributing, flag top CPU consumers
    if breakdown.load > 0:
        culprits.extend(p for p in processes if p.cpu_pct > 50)

    # If I/O is contributing, flag top I/O consumers (privileged)
    if breakdown.io > 0:
        culprits.extend(p for p in processes if p.io_total > 50_000_000)  # 50 MB/s

    # Always flag suspect pattern matches
    culprits.extend(p for p in processes if p.is_suspect)

    return dedupe_by_pid(culprits)
```

### Adaptive Sampling

- **Normal mode:** 5s intervals
- **Elevated mode (stress > 30):** 1s intervals
- **Critical (stress > 60):** Preemptive snapshot capture

### Elevation Triggers

- Load average exceeds core count
- Disk I/O spike (10x baseline)
- Memory available below 20%
- Thermal throttling active
- Self-latency spike (sleep took 50% longer than expected)
- Suspect process exceeds 30% CPU

## Forensics Capture

When a pause is detected (interval > 2x expected):

1. **Immediate process snapshot** (~50ms) - Full process list via psutil
2. **Save tailspin trace** (~1s, privileged) - `sudo tailspin save` captures kernel-level activity *during* the pause
3. **Disk I/O snapshot** (~1s, privileged) - `sudo powermetrics --show-process-io` for per-process I/O
4. **Trigger spindump** (~5-10s, privileged) - `sudo spindump -notarget 5 10 -noProcessingWhileSampling -noBinary -o <path>`
5. **Thermal snapshot** (~1s, privileged) - `sudo powermetrics -n 1` for temps/throttling
6. **Extract system logs** (~1s) - Filter for errors, hangs, memory warnings

### tailspin Integration

`tailspin` is a kernel-level continuous trace buffer built into macOS. Unlike userspace tools (which freeze during pauses), tailspin captures what the kernel was doing during the freeze.

**Setup (one-time):**
```bash
# Enable continuous tracing (persists across reboots)
sudo tailspin enable
```

**On pause detection:**
```bash
# Save the trace buffer (contains activity from before/during/after pause)
sudo tailspin save -o ~/.local/share/pause-monitor/events/<timestamp>/tailspin.trace
```

**Why this matters:** The daemon cannot observe the system *during* a pause because it's also frozen. tailspin is the only way to see kernel-level activity (APFS commits, driver hangs, Secure Enclave operations) that caused the freeze.

### Forensics Storage

```
~/.local/share/pause-monitor/events/<timestamp>/
├── processes.json      # Full process snapshot with CPU/mem
├── tailspin.trace      # Kernel-level activity during pause (privileged)
├── disk_io.json        # Per-process I/O rates (privileged)
├── thermals.json       # CPU temp, freq, throttle state (privileged)
├── spindump.txt        # Thread stacks, 2-5 MB (privileged)
├── system.log          # Filtered system logs
└── summary.json        # Quick overview for TUI/CLI
```

### Log Extraction Query

```bash
log show --start "<pause_start>" --end "<pause_end>" \
    --predicate 'logType == fault OR
                 logType == error OR
                 subsystem CONTAINS "biome" OR
                 process == "kernel"' \
    --style compact
```

### Forensics Health Validation

Forensics commands require sudo access and specific tools to be available. The daemon validates this on startup and periodically (every 5 minutes) to detect permission changes.

```python
from dataclasses import dataclass
from pathlib import Path
import subprocess

@dataclass
class ForensicsHealth:
    """Health status of forensics capabilities."""
    spindump_available: bool = False
    tailspin_available: bool = False
    tailspin_enabled: bool = False
    powermetrics_available: bool = False
    sudo_working: bool = False
    last_check: float = 0.0

    @property
    def fully_operational(self) -> bool:
        return all([
            self.spindump_available,
            self.tailspin_available,
            self.tailspin_enabled,
            self.powermetrics_available,
            self.sudo_working,
        ])

    @property
    def degraded_features(self) -> list[str]:
        """List features that won't work."""
        issues = []
        if not self.sudo_working:
            issues.append("sudo access (all privileged features disabled)")
        elif not self.powermetrics_available:
            issues.append("powermetrics (per-process I/O unavailable)")
        if not self.tailspin_enabled:
            issues.append("tailspin (kernel traces during pause unavailable)")
        if not self.spindump_available:
            issues.append("spindump (thread stacks unavailable)")
        return issues


def check_forensics_health() -> ForensicsHealth:
    """Validate all forensics capabilities.

    Run on daemon startup and periodically to detect permission changes.
    """
    health = ForensicsHealth(last_check=time.time())

    # Check sudo works for our specific commands
    try:
        result = subprocess.run(
            ['sudo', '-n', 'true'],  # -n = non-interactive, fail if password needed
            capture_output=True, timeout=5
        )
        health.sudo_working = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        health.sudo_working = False

    if not health.sudo_working:
        log.warning("forensics_sudo_failed",
                   hint="Run 'pause-monitor install' to configure sudoers")
        return health

    # Check powermetrics
    try:
        result = subprocess.run(
            ['sudo', '-n', 'powermetrics', '-n', '1', '-i', '100',
             '--samplers', 'tasks', '-f', 'plist'],
            capture_output=True, timeout=10
        )
        health.powermetrics_available = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        health.powermetrics_available = False

    # Check tailspin enabled
    try:
        result = subprocess.run(
            ['sudo', '-n', 'tailspin', 'info'],
            capture_output=True, text=True, timeout=5
        )
        health.tailspin_available = result.returncode == 0
        health.tailspin_enabled = 'Recording: enabled' in result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        health.tailspin_available = False
        health.tailspin_enabled = False

    # Check spindump
    health.spindump_available = Path('/usr/sbin/spindump').exists()

    if not health.fully_operational:
        log.warning("forensics_degraded", issues=health.degraded_features)
    else:
        log.info("forensics_healthy")

    return health
```

**Startup behavior:**
1. Run `check_forensics_health()` before entering main loop
2. If `sudo_working = False`, refuse to start with actionable error message
3. If partially degraded, log warning but continue with reduced functionality
4. Store health status for TUI display

**Periodic recheck:**
- Every 5 minutes, re-run health check
- On degradation (was healthy, now not), send notification to user
- Update TUI status indicator

### Forensics Execution Strategy

Forensics captures run in a **separate thread pool** to avoid blocking the sampling loop:

```python
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable

class ForensicsExecutor:
    """Execute forensics captures without blocking sampling."""

    def __init__(self, max_workers: int = 2):
        # 2 workers allows concurrent spindump + other captures
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='forensics')
        self._pending: list[Future] = []

    def capture_pause_event(self, event_dir: Path, pause_duration: float) -> None:
        """Trigger full forensics capture for a pause event.

        Operations run concurrently where possible:
        - Group 1 (fast, run first): process snapshot, tailspin save
        - Group 2 (slow, run after): spindump, log extraction
        """
        # Submit all captures - they'll run concurrently up to max_workers
        futures = [
            self._executor.submit(self._capture_processes, event_dir),
            self._executor.submit(self._capture_tailspin, event_dir),
            self._executor.submit(self._capture_spindump, event_dir),
            self._executor.submit(self._capture_logs, event_dir, pause_duration),
        ]
        self._pending.extend(futures)

        # Clean up completed futures periodically
        self._pending = [f for f in self._pending if not f.done()]

    def _capture_processes(self, event_dir: Path) -> bool:
        """Capture process list (fast, ~50ms)."""
        # ... implementation

    def _capture_tailspin(self, event_dir: Path) -> bool:
        """Save tailspin buffer (fast, ~1s)."""
        # ... implementation

    def _capture_spindump(self, event_dir: Path) -> bool:
        """Run spindump (slow, 5-10s)."""
        # ... implementation

    def _capture_logs(self, event_dir: Path, pause_duration: float) -> bool:
        """Extract system logs (medium, ~1-2s)."""
        # ... implementation

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown executor, optionally waiting for pending captures."""
        self._executor.shutdown(wait=wait)
```

**Key design decisions:**
- **2 workers:** Allows tailspin + process snapshot to run while spindump is running
- **Non-blocking:** Sampling loop submits captures and continues immediately
- **Cleanup:** Completed futures are pruned to prevent memory growth
- **Graceful shutdown:** Pending captures can complete before daemon exits

## TUI Dashboard

```
┌─ pause-monitor ──────────────────────────────────────────── 09:32:15 ─┐
│                                                                        │
│  SYSTEM HEALTH          STRESS: ██░░░░░░░░ 12%        Mode: Normal 5s │
│  ───────────────────────────────────────────────────────────────────── │
│  CPU:  ████████░░░░░░░░ 47%    Load: 3.4/16    ANE: idle              │
│  Mem:  ████████████░░░░ 73%    Avail: 34 GB    Pressure: Low          │
│  I/O:  ██░░░░░░░░░░░░░░  8%    R: 12 MB/s     W: 45 MB/s              │
│  Temp: ████░░░░░░░░░░░░ 52°C   Throttle: No   GPU: 23%                │
│                                                                        │
│  TOP PROCESSES (by energy impact)                    CPU   MEM    I/O │
│  ───────────────────────────────────────────────────────────────────── │
│  claude -c                                           106%  4.5%  2 MB/s│
│  claude                                               87%  0.5%  0 MB/s│
│  mdworker_shared  [→Spotlight]                       12%  0.1% 89 MB/s│
│  ghostty                                              24% 57.4%  1 MB/s│
│  WindowServer                                         20%  0.2%  0 MB/s│
│                                                                        │
│  RECENT EVENTS                                                         │
│  ───────────────────────────────────────────────────────────────────── │
│  ⚠ 09:10:13  PAUSE 74.3s  [biomesyncd suspected]         [View: Enter]│
│  ● 03:44:01  Elevated sampling triggered (I/O spike)                   │
│  ● Yesterday 22:15  PAUSE 12.1s  [BDLDaemon suspected]                 │
│                                                                        │
│  [q] Quit  [e] Events  [p] Processes  [h] History  [?] Help           │
└────────────────────────────────────────────────────────────────────────┘
```

### Elevated State Display

When stress exceeds 30%, the dashboard highlights contributing factors and suspected processes:

```
┌─ pause-monitor ──────────────────────────────────────────── 14:22:07 ─┐
│                                                                        │
│  ⚠ ELEVATED             STRESS: ████████░░ 78%      Mode: Elevated 1s │
│  ───────────────────────────────────────────────────────────────────── │
│  STRESS BREAKDOWN                                                      │
│    Load:     ████████████████ +32  (load 4.2 on 2 cores)              │
│    I/O:      ████████████░░░░ +20  (spike: 340 MB/s write)            │
│    Memory:   ██████░░░░░░░░░░ +12  (14% available)                    │
│    Thermal:  ░░░░░░░░░░░░░░░░  +0  (not throttled)                    │
│    Latency:  ██████████░░░░░░ +14  (1.7x expected)                    │
│                                                                        │
│  SUSPECTED CULPRITS                                   CPU   MEM    I/O │
│  ───────────────────────────────────────────────────────────────────── │
│  ★ mdworker_shared                                    45%  0.2% 298 MB/s│
│  ★ mds_stores                                         89%  1.2%  42 MB/s│
│    kernel_task                                       112%  0.0%   0 MB/s│
│    claude -c                                         106%  4.5%   2 MB/s│
│                                                                        │
│  ● 14:21:58  Elevated: I/O spike (mdworker_shared)                    │
│  ● 14:21:45  Elevated: Load exceeded cores                            │
│                                                                        │
│  [q] Quit  [e] Events  [p] Processes  [h] History  [?] Help           │
└────────────────────────────────────────────────────────────────────────┘
```

**Key features:**
- **Stress breakdown** shows contribution from each factor with visual bars
- **Suspected culprits** (★) are processes that correlate with stress factors:
  - High I/O process when I/O is spiking
  - High CPU process when load exceeds cores
  - Processes matching suspect patterns
- **Timeline** shows what triggered elevation and when

**Apple Silicon / powermetrics enhancements (privileged mode):**
- **ANE status** replaces CPU frequency (which isn't available on Apple Silicon)
- **Energy impact sorting** - processes sorted by Apple's composite energy score, not just CPU%
- **Responsible PID** - XPC helpers show their parent app in brackets (e.g., `mdworker_shared [→Spotlight]`)
- **Coalition grouping** - related processes can be viewed as a group in the Processes view

### Views

- **Dashboard** (default) - System overview
- **Processes** - Full process list, sortable
- **Events** - Pause history with filters
- **History** - Charts of metrics over time

### Data Refresh

The TUI polls the SQLite database for updates:

- **Refresh interval:** 1 second (matches elevated sampling)
- **Query:** Latest sample + recent events
- **Connection:** Read-only mode (`?mode=ro`) to avoid blocking daemon writes

```python
# TUI refresh loop (Textual timer)
def compose(self) -> ComposeResult:
    yield Dashboard()
    self.set_interval(1.0, self.refresh_data)

async def refresh_data(self) -> None:
    # URI format required for mode=ro parameter
    # Note: Must use file: prefix for URI mode, and Path must be converted to string
    db_uri = f"file:{DB_PATH}?mode=ro"
    async with aiosqlite.connect(db_uri, uri=True) as db:
        async with db.execute(
            "SELECT * FROM samples ORDER BY id DESC LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            self.query_one(Dashboard).update(row)
```

The daemon and TUI can run simultaneously—WAL mode ensures reads don't block writes.

## Alerting

Goal #3 requires knowing when the system is under stress *without* actively watching the TUI. The daemon supports native macOS notifications for critical events.

### Notification Triggers

| Event | Default | Configurable |
|-------|---------|--------------|
| Pause detected (>2s) | Always notify | Minimum duration threshold |
| Critical stress (>60) sustained for 30s | Enabled | Threshold, duration |
| Elevated mode entered | Disabled | Enable/disable |
| Forensics capture completed | Enabled | Enable/disable |

### Implementation

macOS notifications via `osascript` (no dependencies) or `terminal-notifier` (if installed):

```python
import subprocess
import shutil

def notify(title: str, message: str, sound: bool = True) -> None:
    """Send macOS notification.

    Uses terminal-notifier if available (better UX), falls back to osascript.
    """
    # Prefer terminal-notifier (supports clicking to open app, better styling)
    if shutil.which('terminal-notifier'):
        cmd = [
            'terminal-notifier',
            '-title', 'pause-monitor',
            '-subtitle', title,
            '-message', message,
            '-group', 'pause-monitor',  # Coalesce repeated notifications
        ]
        if sound:
            cmd.extend(['-sound', 'Basso'])
        subprocess.run(cmd, capture_output=True)
        return

    # Fallback: osascript (always available, no install required)
    sound_clause = 'sound name "Basso"' if sound else ''
    script = f'''
    display notification "{message}" with title "pause-monitor" subtitle "{title}" {sound_clause}
    '''
    subprocess.run(['osascript', '-e', script], capture_output=True)


def notify_pause(duration: float, suspects: list[str]) -> None:
    """Notify user of detected pause."""
    suspect_text = f" Suspects: {', '.join(suspects)}" if suspects else ""
    notify(
        f"System Pause: {duration:.1f}s",
        f"Forensics captured.{suspect_text}",
        sound=True
    )


def notify_critical_stress(stress: int, top_factor: str) -> None:
    """Notify user of sustained critical stress."""
    notify(
        f"Critical Stress: {stress}%",
        f"Primary factor: {top_factor}. System may pause soon.",
        sound=True
    )
```

### Configuration

```toml
# ~/.config/pause-monitor/config.toml

[alerts]
enabled = true                    # Master switch for all alerts
pause_detected = true             # Alert on pause events
pause_min_duration = 2.0          # Minimum pause duration to alert (seconds)
critical_stress = true            # Alert on sustained critical stress
critical_threshold = 60           # Stress level for critical alert
critical_duration = 30            # Seconds stress must be sustained
elevated_entered = false          # Alert when entering elevated mode
forensics_completed = true        # Alert when forensics capture finishes
sound = true                      # Play sound with notifications
```

### Why Notifications Over Other Approaches

| Approach | Pros | Cons |
|----------|------|------|
| **macOS Notifications** | Native UX, no setup, works when screen locked | Can be silenced via DND |
| Email/Webhook | Remote notification | Requires configuration, network |
| Menu bar icon | Always visible | Requires separate app/agent |
| Sound only | Hard to miss | Annoying, no context |

Native notifications provide the best balance of visibility and zero-configuration. Users who need remote alerts can set up webhook forwarding via Shortcuts.app automation.

## CLI Interface

```
pause-monitor
├── daemon      # Run the background sampler
├── tui         # Launch interactive dashboard
├── status      # Quick health check (one-liner)
├── events      # List/inspect pause events
├── history     # Query historical data
├── config      # Manage configuration
├── install     # Set up launchd service
├── uninstall   # Remove launchd service
└── prune       # Manual data cleanup
```

### Example Usage

```bash
# Quick status check
$ pause-monitor status
✓ Healthy | Stress: 12% | Load: 3.4/16 | Mem: 73% | Last pause: 2h ago

# List recent pause events
$ pause-monitor events
ID  TIMESTAMP            DURATION  SUSPECT
3   2026-01-20 09:10:13  74.3s     biomesyncd (95% CPU before pause)
2   2026-01-19 22:15:01  12.1s     BDLDaemon (I/O spike)

# Inspect a specific event
$ pause-monitor events 3

# Show what was happening at a specific time
$ pause-monitor history --at "2026-01-20 09:08:55"
```

## Project Structure

```
pause-monitor/
├── pyproject.toml
├── CLAUDE.md
├── AGENTS.md
├── README.md
├── LICENSE
├── docs/
│   └── plans/
│       └── 2026-01-20-pause-monitor-design.md
├── src/
│   └── pause_monitor/
│       ├── __init__.py
│       ├── __main__.py      # Entry point
│       ├── cli.py           # CLI commands (click)
│       ├── daemon.py        # Background sampler
│       ├── collector.py     # Streaming powermetrics + plist parsing
│       ├── storage.py       # SQLite operations
│       ├── forensics.py     # Pause event capture
│       ├── stress.py        # Stress score calculation
│       └── tui/
│           ├── __init__.py
│           ├── app.py       # Textual app
│           └── widgets.py   # Custom widgets
└── tests/
```

## Dependencies

| Package | Purpose |
|---------|---------|
| `textual` | Modern TUI framework |
| `rich` | Pretty CLI output |
| `click` | CLI framework |
| `aiosqlite` | Async SQLite for non-blocking TUI database reads |
| `structlog` | Structured daemon logging with context binding |
| `tomlkit` | TOML config read/write with comment preservation |

**Not required:**
- `psutil` — powermetrics provides all process and system metrics
- `pyobjc` — sleep/wake detection uses `pmset` and clock drift (no Cocoa APIs needed)

The stdlib `plistlib` handles powermetrics output parsing.

## Installation

```bash
# Development
uv run pause-monitor daemon
uv run pause-monitor tui

# Install globally
uv tool install .

# Set up daemon (creates launchd plist, sudoers rules, enables tailspin)
pause-monitor install
```

### Install Command Details

The `pause-monitor install` command performs several security-sensitive operations. It's designed to fail safely and provide clear feedback.

**Install Steps:**

```python
def install_command():
    """Set up pause-monitor for background operation."""

    # 1. Verify we're running as the correct user (not root)
    if os.getuid() == 0:
        raise click.ClickException(
            "Don't run install as root. Run as your normal user - it will prompt for sudo when needed."
        )

    # 2. Check prerequisites
    print("Checking prerequisites...")
    check_macos_version()  # Require macOS 12+ for tailspin features
    check_admin_group()    # User must be in admin group for sudo

    # 3. Create data directories
    print("Creating data directories...")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # 4. Initialize database (if not exists)
    if not DB_PATH.exists():
        print("Initializing database...")
        init_database(DB_PATH)

    # 5. Generate and install sudoers rules
    print("Configuring sudo access (will prompt for password)...")
    install_sudoers()

    # 6. Enable tailspin
    print("Enabling tailspin continuous tracing...")
    enable_tailspin()

    # 7. Generate and install launchd plist
    print("Installing launchd service...")
    install_launchd()

    # 8. Validate installation
    print("Validating installation...")
    health = check_forensics_health()
    if not health.fully_operational:
        print(f"Warning: Some features unavailable: {health.degraded_features}")

    print("Installation complete! Start with: pause-monitor daemon")
    print("Or enable auto-start: launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.local.pause-monitor.plist")


def install_sudoers():
    """Install sudoers rules for privileged operations.

    Security considerations:
    - Uses specific username, not wildcards
    - Output paths constrained to user's directory
    - Validates with visudo before installing
    - Creates backup of any existing rules
    """
    import getpass
    import tempfile

    username = getpass.getuser()
    home = Path.home()

    sudoers_content = f'''# /etc/sudoers.d/pause-monitor-{username}
# Generated by: pause-monitor install
# User: {username}
# Safe to remove: sudo rm /etc/sudoers.d/pause-monitor-{username}

# powermetrics: streaming mode for continuous monitoring
{username} ALL=(root) NOPASSWD: /usr/bin/powermetrics -i * --samplers cpu_power\\,gpu_power\\,thermal\\,tasks\\,ane_power\\,disk --show-process-io --show-process-gpu --show-process-coalition --show-responsible-pid --show-process-energy --show-process-samp-norm --handle-invalid-values -f plist

# spindump: thread stack capture on pause
{username} ALL=(root) NOPASSWD: /usr/sbin/spindump -notarget 5 10 -noProcessingWhileSampling -noBinary -o {home}/.local/share/pause-monitor/events/*

# tailspin: kernel trace save and enable
{username} ALL=(root) NOPASSWD: /usr/bin/tailspin save -o {home}/.local/share/pause-monitor/events/*
{username} ALL=(root) NOPASSWD: /usr/bin/tailspin enable
'''

    # Write to temp file and validate with visudo
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sudoers', delete=False) as f:
        f.write(sudoers_content)
        temp_path = f.name

    try:
        # Validate syntax
        result = subprocess.run(
            ['sudo', 'visudo', '-c', '-f', temp_path],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise click.ClickException(f"Invalid sudoers syntax: {result.stderr}")

        # Install (will prompt for password)
        sudoers_path = f'/etc/sudoers.d/pause-monitor-{username}'
        subprocess.run(
            ['sudo', 'cp', temp_path, sudoers_path],
            check=True
        )
        subprocess.run(
            ['sudo', 'chmod', '440', sudoers_path],
            check=True
        )
    finally:
        os.unlink(temp_path)


def enable_tailspin():
    """Enable tailspin continuous tracing."""
    result = subprocess.run(
        ['sudo', 'tailspin', 'enable'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        # Not fatal - tailspin may already be enabled or not supported
        log.warning("tailspin_enable_failed", stderr=result.stderr)
```

**Uninstall Command:**

```python
def uninstall_command():
    """Remove pause-monitor from the system."""
    import getpass
    username = getpass.getuser()

    # 1. Stop daemon if running
    subprocess.run(
        ['launchctl', 'bootout', f'gui/{os.getuid()}/com.local.pause-monitor'],
        capture_output=True
    )

    # 2. Remove launchd plist
    plist_path = Path.home() / 'Library/LaunchAgents/com.local.pause-monitor.plist'
    if plist_path.exists():
        plist_path.unlink()
        print("Removed launchd plist")

    # 3. Remove sudoers rules
    subprocess.run(
        ['sudo', 'rm', '-f', f'/etc/sudoers.d/pause-monitor-{username}'],
        check=True
    )
    print("Removed sudoers rules")

    # 4. Optionally remove data (prompt user)
    if click.confirm("Remove all data (database, events, config)?"):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
        shutil.rmtree(CONFIG_DIR, ignore_errors=True)
        print("Removed all data")

    print("Uninstall complete")
```

### launchd Plist

`pause-monitor install` generates `~/Library/LaunchAgents/com.local.pause-monitor.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.local.pause-monitor</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/hunter/.local/bin/pause-monitor</string>
        <string>daemon</string>
    </array>

    <!-- Keep daemon running -->
    <key>KeepAlive</key>
    <true/>

    <!-- Restart quickly after crash -->
    <key>ThrottleInterval</key>
    <integer>5</integer>

    <!-- Don't hog CPU during high load -->
    <key>Nice</key>
    <integer>5</integer>

    <!-- CRITICAL: Prevent App Nap timer coalescing -->
    <key>LegacyTimers</key>
    <true/>

    <!-- Start at login -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Logging -->
    <key>StandardOutPath</key>
    <string>/Users/hunter/.local/share/pause-monitor/daemon.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/hunter/.local/share/pause-monitor/daemon.log</string>
</dict>
</plist>
```

**Key settings:**
- `LegacyTimers`: Prevents macOS from coalescing timers, ensuring accurate 5s/1s sampling
- `Nice`: Low priority so the daemon doesn't contribute to system stress
- `KeepAlive`: Auto-restart on crash

**Management (modern launchctl syntax):**
```bash
# Start daemon (bootstrap replaces deprecated 'load')
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.local.pause-monitor.plist

# Stop daemon (bootout replaces deprecated 'unload')
launchctl bootout gui/$(id -u)/com.local.pause-monitor

# Check status
launchctl print gui/$(id -u)/com.local.pause-monitor

# Legacy commands (deprecated but still work)
# launchctl load/unload - avoid these in new code
```

Note: `gui/$(id -u)` targets the current user's GUI domain. The `load`/`unload` commands are deprecated since macOS 10.11 but still function.

## Data Locations

- **Config:** `~/.config/pause-monitor/config.toml`
- **Database:** `~/.local/share/pause-monitor/data.db`
- **Events:** `~/.local/share/pause-monitor/events/`
- **Daemon log:** `~/.local/share/pause-monitor/daemon.log`

## Configuration

### Config File Location and Loading

```python
from pathlib import Path
from dataclasses import dataclass, field
import tomlkit

CONFIG_PATH = Path.home() / ".config" / "pause-monitor" / "config.toml"

@dataclass
class SamplingConfig:
    normal_interval: int = 5
    elevated_interval: int = 1
    elevation_threshold: int = 30
    critical_threshold: int = 60

@dataclass
class RetentionConfig:
    samples_days: int = 30
    events_days: int = 90

@dataclass
class AlertsConfig:
    enabled: bool = True
    pause_detected: bool = True
    pause_min_duration: float = 2.0
    critical_stress: bool = True
    critical_threshold: int = 60
    critical_duration: int = 30
    elevated_entered: bool = False
    forensics_completed: bool = True
    sound: bool = True

@dataclass
class SuspectsConfig:
    patterns: list[str] = field(default_factory=lambda: [
        "codemeter", "bitdefender", "biomesyncd", "motu",
        "coreaudiod", "kernel_task", "WindowServer"
    ])

@dataclass
class Config:
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    suspects: SuspectsConfig = field(default_factory=SuspectsConfig)
    learning_mode: bool = False

    @classmethod
    def load(cls) -> "Config":
        """Load config from file, creating default if missing."""
        if not CONFIG_PATH.exists():
            config = cls()
            config.save()
            return config

        with open(CONFIG_PATH) as f:
            data = tomlkit.load(f)

        # Merge loaded data with defaults (handles missing keys gracefully)
        return cls(
            sampling=SamplingConfig(**data.get('sampling', {})),
            retention=RetentionConfig(**data.get('retention', {})),
            alerts=AlertsConfig(**data.get('alerts', {})),
            suspects=SuspectsConfig(**data.get('suspects', {})),
            learning_mode=data.get('learning_mode', False),
        )

    def save(self) -> None:
        """Save config to file, preserving comments if updating existing."""
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        # tomlkit preserves comments and formatting when editing
        # ... implementation
```

**Hot-reload via SIGHUP:**

The daemon reloads config when it receives SIGHUP:

```python
import signal

def reload_config(signum, frame):
    """SIGHUP handler: reload config without restart."""
    global config
    try:
        new_config = Config.load()
        config = new_config
        log.info("config_reloaded")
    except Exception as e:
        log.error("config_reload_failed", error=str(e))
        # Keep old config on failure

signal.signal(signal.SIGHUP, reload_config)
```

**Validation:** Config values are validated on load:
- Intervals must be positive integers
- Thresholds must be 0-100
- Patterns must be non-empty strings

Invalid values log a warning and fall back to defaults.

### Config File Format

```toml
# ~/.config/pause-monitor/config.toml

# Enable learning mode for first-time calibration (recommended for 1-2 weeks)
learning_mode = false

[sampling]
normal_interval = 5       # seconds
elevated_interval = 1     # seconds
elevation_threshold = 30  # stress score
critical_threshold = 60   # stress score

[retention]
samples_days = 30
events_days = 90

[alerts]
enabled = true
pause_detected = true
pause_min_duration = 2.0
critical_stress = true
critical_threshold = 60
critical_duration = 30
elevated_entered = false
forensics_completed = true
sound = true

[suspects]
# User-configurable process name patterns to flag as "suspects"
# These are HINTS based on community reports, not reliable predictors.
# The daemon will highlight matching processes when they're active during stress,
# but correlation-based culprit identification (top CPU/I/O during stress) is primary.
#
# Default patterns are processes commonly reported to cause macOS pauses:
#   - codemeter: DRM license manager, known for I/O storms
#   - bitdefender: Antivirus with aggressive scanning
#   - biomesyncd: Apple's biome sync, occasional runaway
#   - motu: Audio driver, can cause priority inversions
#   - coreaudiod: Audio subsystem, rare but impactful when it misbehaves
#   - kernel_task: High kernel_task often indicates APFS/thermal issues
#   - WindowServer: UI compositor, can stall entire display
#
# Add your own patterns based on observed correlations on YOUR system.
# Remove patterns that create false positives for your workflow.
patterns = [
    "codemeter",
    "bitdefender",
    "biomesyncd",
    "motu",
    "coreaudiod",
    "kernel_task",
    "WindowServer",
]

# Future: The daemon may learn suspect patterns automatically by correlating
# process activity with detected pauses over time. For now, this is manual.
```

## macOS-Specific Considerations

### Sleep/Wake Detection

On macOS, `time.monotonic()` uses `mach_absolute_time()` which **pauses** during sleep—the monotonic clock does NOT advance while the system is asleep. However, this doesn't help us directly because:

1. The daemon process is **suspended** during sleep (can't observe anything)
2. When the system wakes, the daemon resumes with a tiny monotonic delta (milliseconds from suspend to resume)
3. But `time.time()` (wall clock) will have advanced by the full sleep duration

**The problem:** A large wall-clock gap with a tiny monotonic gap indicates sleep. A large gap in **both** clocks indicates an actual system pause (the daemon was frozen but the system was awake).

The daemon detects sleep/wake events to distinguish "system was asleep" from "system was frozen."

**Primary Detection Strategy (pmset log):**

The simplest and most reliable approach parses `pmset -g log`, which records all power events with timestamps:

```python
import subprocess
import re
from datetime import datetime

# Regex for pmset log timestamps: "2026-01-20 09:15:23 -0800"
PMSET_TIMESTAMP_RE = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')

def check_recent_wake(seconds_ago: int = 30) -> tuple[bool, str | None]:
    """Check pmset log for recent wake event.

    Returns:
        (is_recent_wake, wake_type) where wake_type is 'Sleep', 'Wake', or 'DarkWake'
    """
    result = subprocess.run(
        ['pmset', '-g', 'log'],
        capture_output=True, text=True, timeout=5
    )

    now = datetime.now()
    # Scan recent entries (newest last)
    for line in reversed(result.stdout.splitlines()[-100:]):
        # Look for wake/sleep events
        if 'Wake from' in line or 'DarkWake' in line or 'Sleep' in line:
            match = PMSET_TIMESTAMP_RE.search(line)
            if match:
                event_time = datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S')
                age = (now - event_time).total_seconds()
                if age < seconds_ago:
                    wake_type = 'DarkWake' if 'DarkWake' in line else (
                        'Wake' if 'Wake' in line else 'Sleep'
                    )
                    return True, wake_type
    return False, None

def is_darkwake() -> bool:
    """Check if currently in DarkWake (Power Nap) state.

    During DarkWake, the system is technically awake but the display is off.
    Samples taken during DarkWake should be flagged as potentially anomalous.
    """
    result = subprocess.run(
        ['pmset', '-g', 'assertions'],
        capture_output=True, text=True, timeout=5
    )
    return 'PreventUserIdleSystemSleep' not in result.stdout and \
           'NoDisplaySleepAssertion' not in result.stdout
```

**Why pmset over NSWorkspace notifications:**
- No pyobjc dependency required for this single feature
- No thread/runloop complexity
- Works reliably across all macOS versions
- Provides wake *type* (full wake vs DarkWake) for better filtering

**Alternative: Clock drift detection (no subprocess):**

For very low-overhead detection, compare monotonic and wall clock drift:

```python
import time

class ClockDriftDetector:
    """Detect sleep by comparing monotonic vs wall clock drift."""

    def __init__(self):
        self.last_monotonic = time.monotonic()
        self.last_wall = time.time()

    def check(self) -> tuple[bool, float]:
        """Check for sleep since last call.

        Returns:
            (was_asleep, sleep_duration_seconds)
        """
        now_monotonic = time.monotonic()
        now_wall = time.time()

        monotonic_delta = now_monotonic - self.last_monotonic
        wall_delta = now_wall - self.last_wall

        self.last_monotonic = now_monotonic
        self.last_wall = now_wall

        # If wall clock advanced much more than monotonic, system was asleep
        # Monotonic clock pauses during sleep, wall clock does not
        drift = wall_delta - monotonic_delta

        # Allow 2 seconds of drift for clock adjustments
        if drift > 2.0:
            return True, drift
        return False, 0.0
```

This approach requires no subprocess calls and catches sleep events in the sampling loop itself. Use it as the primary fast-path check, with pmset for detailed wake type classification when needed.

**On large interval detected:**
1. Check if `is_recent_wake()` returns True
2. If yes: log as sleep event, don't record as pause
3. If no: this was an actual system pause, trigger forensics

### App Nap Mitigation

macOS may throttle background daemons via App Nap, reducing timer resolution from milliseconds to seconds. This would undermine pause detection accuracy.

**Preferred: caffeinate subprocess (simple, no pyobjc required):**
```python
import subprocess
import os

# Spawn caffeinate tied to our process - it exits when we exit
caffeinate_proc = subprocess.Popen(
    ['caffeinate', '-i', '-w', str(os.getpid())],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL
)
# -i: prevent idle sleep
# -w: exit when process with given PID exits
```

**Alternative: NSProcessInfo (requires pyobjc-framework-Cocoa):**
```python
from AppKit import NSProcessInfo

# Disable App Nap for the daemon process
info = NSProcessInfo.processInfo()
activity = info.beginActivityWithOptions_reason_(
    NSProcessInfo.NSActivityUserInitiatedAllowingIdleSystemSleep,
    "Monitoring system health for pause detection"
)
# Store `activity` reference to prevent garbage collection
```

**For launchd-managed daemons:** The `LegacyTimers` key in the plist prevents timer coalescing:
```xml
<key>LegacyTimers</key>
<true/>
```

### Compressed Memory

macOS uses memory compression before swapping. Track compression health:

```python
# From vm_stat output:
# - Pages stored in compressor
# - Pages occupied by compressor
# Healthy ratio: 15-20x, Stressed: <5x
compression_ratio = pages_stored / pages_occupied
if compression_ratio < 5:
    memory_pressure = "critical"
```

### Apple Silicon Considerations

On M-series chips:
- **P-cores vs E-cores:** Load average doesn't distinguish core types. Saturated P-cores are more concerning than saturated E-cores. powermetrics provides per-cluster CPU time in the plist output under `processor/clusters/*/cpu_power` which can be parsed for more accurate attribution.
- **Unified Memory:** GPU and CPU share memory. High GPU utilization with low available memory indicates unified memory pressure.
- **Neural Engine:** Heavy ANE usage can cause system pauses. Monitor via `powermetrics --samplers ane_power`.
- **CPU Frequency:** Not available via standard interfaces—`psutil.cpu_freq()` returns placeholder values. The TUI shows ANE status instead.
- **QoS Scheduling:** Apple Silicon aggressively uses QoS classes. A process marked `QOS_CLASS_BACKGROUND` may appear "slow" but is working as designed.

### Privacy Permissions (TCC)

macOS's Transparency, Consent, and Control (TCC) system may require additional permissions:

**Full Disk Access (FDA):**
- Required for reading certain system logs
- Required for accessing other processes' working directories
- May affect some `powermetrics` operations on protected processes

**How to grant:** System Preferences → Privacy & Security → Full Disk Access → Add Terminal.app (or the terminal emulator running the daemon)

**Detection:** The daemon should check for FDA on startup and warn if missing:
```python
def check_full_disk_access() -> bool:
    """Check if we have Full Disk Access (required for some operations)."""
    # Try to read a TCC-protected location
    test_path = Path.home() / "Library/Safari/Bookmarks.plist"
    try:
        test_path.read_bytes()
        return True
    except PermissionError:
        return False
```

**Note:** The daemon functions without FDA but some forensics data may be incomplete.

## Privileged Operations

Several valuable metrics require root access on macOS. Rather than running the entire daemon as root, we use a **sudoers.d approach** that grants passwordless access to specific, constrained commands.

### Why Not Run as Root?

- Larger attack surface
- Unnecessary for most operations (psutil works fine unprivileged)
- Violates principle of least privilege

### Installation

During `pause-monitor install`, we create a user-specific sudoers rule. The installer generates rules with the **actual username** (not wildcards) to prevent cross-user path traversal attacks.

```bash
# /etc/sudoers.d/pause-monitor-hunter
# Generated by: pause-monitor install
# User: hunter
#
# SECURITY: Paths are constrained to this specific user's directory.
# Wildcards like /Users/*/ would allow writing to any user's home directory.

# spindump: 5 second sample, 10ms interval, constrained output path
hunter ALL=(root) NOPASSWD: /usr/sbin/spindump -notarget 5 10 -noProcessingWhileSampling -noBinary -o /Users/hunter/.local/share/pause-monitor/events/*

# powermetrics: streaming mode for continuous monitoring
# The daemon runs powermetrics as a long-lived subprocess, so we allow the streaming command.
# Samplers:
#   cpu_power  - CPU frequency, power, throttling (note: freq hidden on Apple Silicon)
#   gpu_power  - GPU utilization and power
#   thermal    - thermal pressure state
#   tasks      - per-process CPU and scheduling info
#   ane_power  - Neural Engine activity (ML workloads can cause pauses)
#   disk       - system-wide disk I/O metrics
# Per-process options:
#   --show-process-io         - per-process disk I/O (the key metric psutil can't provide)
#   --show-process-gpu        - per-process GPU utilization
#   --show-process-coalition  - groups related processes (app + XPC helpers)
#   --show-responsible-pid    - maps XPC helper activity to parent app
#   --show-process-energy     - Apple's composite energy impact score
#   --show-process-samp-norm  - CPU % normalized to sample window (more useful than process uptime)
#   --handle-invalid-values   - output invalid=true instead of aborting on hardware errors
hunter ALL=(root) NOPASSWD: /usr/bin/powermetrics -i * --samplers cpu_power\,gpu_power\,thermal\,tasks\,ane_power\,disk --show-process-io --show-process-gpu --show-process-coalition --show-responsible-pid --show-process-energy --show-process-samp-norm --handle-invalid-values -f plist

# tailspin: save kernel trace buffer on pause detection (captures activity during freeze)
hunter ALL=(root) NOPASSWD: /usr/bin/tailspin save -o /Users/hunter/.local/share/pause-monitor/events/*

# tailspin enable: one-time setup to enable continuous tracing
hunter ALL=(root) NOPASSWD: /usr/bin/tailspin enable
```

**Security notes:**
- Rules use specific username (`hunter`), not `%admin` with path wildcards
- The `-i *` allows both 5000ms and 1000ms intervals without separate rules
- Output paths constrained to user's own data directory
- File is named `pause-monitor-<username>` to support multi-user systems
- Validated with `visudo -c` during installation

The daemon then calls these via `sudo` without password prompts.

### Privileged Metrics

| Metric | Source | Why It Matters |
|--------|--------|----------------|
| **Kernel trace** | `tailspin save` | Captures kernel-level activity *during* the pause—the only way to see what caused freezes |
| **Per-process disk I/O** | `powermetrics --show-process-io` | Identifies which process is hammering the disk—often the pause culprit |
| **Per-process GPU** | `powermetrics --show-process-gpu` | Identifies GPU-bound processes causing stalls |
| **Process coalitions** | `powermetrics --show-process-coalition` | Groups related processes (e.g., app + XPC helpers) for accurate attribution |
| **Responsible PID** | `powermetrics --show-responsible-pid` | Maps XPC helper activity to the parent app—essential for modern macOS apps |
| **Energy impact** | `powermetrics --show-process-energy` | Apple's composite score (what Activity Monitor shows)—good overall "problem process" indicator |
| **Neural Engine** | `powermetrics --samplers ane_power` | ML workloads can cause priority inversions and pauses |
| **System disk I/O** | `powermetrics --samplers disk` | System-wide disk metrics complement per-process I/O |
| **CPU temperature** | `powermetrics --samplers thermal` | High temps trigger throttling |
| **Thermal throttling** | `powermetrics --samplers cpu_power` | Direct indicator of degraded performance |
| **CPU frequency** | `powermetrics --samplers cpu_power` | Shows if running at boost or base clock (**note: not available on Apple Silicon**) |
| **GPU utilization** | `powermetrics --samplers gpu_power` | GPU-bound workloads can stall the system |
| **Thread stacks** | `spindump` | Post-pause forensics showing what threads were doing |

### Privileged Mode Required

This tool requires privileged mode to function. Without powermetrics, the core value proposition—identifying *which process* is causing I/O storms, thermal throttling, or resource contention—is impossible.

**Installation requirement:** `pause-monitor install` sets up sudoers rules and enables tailspin. The daemon will refuse to start without proper privileged access configured.

### Security Considerations

- Commands are **exact patterns**—no shell injection possible
- Output paths constrained to user directories
- Only `%admin` group (macOS default for admin users) gets access
- Easily removed: `sudo rm /etc/sudoers.d/pause-monitor`

## Known Limitations

This design has inherent limitations that users should understand:

### Fundamental Constraints

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| **Daemon frozen during pauses** | Cannot capture data *during* a freeze—only before and after | Capture pre-pause state; use continuous `tailspin` for during-pause data |
| **5s sampling misses short pauses** | Pauses <5 seconds undetected in normal mode | Elevated mode (1s) catches more, but still misses <1s freezes |
| **Kernel-level I/O invisible** | psutil can't see Spotlight, APFS, FileVault activity—often the actual cause | Privileged `powermetrics --show-process-io` helps; `fs_usage` provides more detail |
| **Stress score is heuristic** | Arbitrary weights (load=40, memory=30, etc.) are not empirically validated | Collect data first, tune weights based on actual pause correlations |
| **Post-pause forensics lag** | By the time spindump runs, the culprit process may have finished | Pre-emptive snapshots at critical stress help; consider continuous `tailspin` |

### What This Tool Cannot Do

- **Predict pauses with certainty** — Elevated stress often precedes pauses, but not always
- **Identify kernel-level causes** — Pauses from APFS commits, GPU driver hangs, or Secure Enclave operations are invisible to userspace
- **Capture data during complete freezes** — If the entire system stops, so does this daemon
- **Distinguish P-core vs E-core saturation** — Load average doesn't differentiate (Apple Silicon)

### Validation Needed

Before trusting the stress score, users should:

1. Run in "learning mode" for 1-2 weeks (collect data, don't alert)
2. Correlate any observed pauses with stress scores at that time
3. Adjust weights if stress doesn't predict pauses on their hardware
4. Consider their specific Mac model (thresholds differ for MacBook Air vs Mac Studio)

## Alternative Approaches Considered

During design, these alternative approaches were evaluated:

### DTrace / Instruments

macOS has built-in kernel tracing:

```bash
# DTrace: observe I/O latency at kernel level
sudo dtrace -n 'io:::start { @[execname] = count(); }'

# Instruments: record comprehensive system trace
xcrun xctrace record --template 'System Trace' --time-limit 30s
```

**Why not used:** DTrace requires SIP partial disable on modern macOS. Instruments produces large traces better suited for manual analysis than continuous monitoring. However, for deep debugging, these tools are superior.

### Continuous spindump / tailspin

macOS's `tailspin` records lightweight continuous kernel traces. **This daemon integrates tailspin directly**—on pause detection, it saves the trace buffer automatically. See "Forensics Capture" and "tailspin Integration" sections.

Setup is part of `pause-monitor install`:
```bash
sudo tailspin enable  # One-time, persists across reboots
```

### sysdiagnose

Apple's diagnostic bundle captures everything:

```bash
# Triggered manually or via keyboard shortcut
sudo sysdiagnose -u
```

**Trade-off:** Takes 5-10 minutes, produces 200+ MB. Best for occasional deep dives, not continuous monitoring. Consider triggering automatically after severe pauses.

### Kernel Extension / System Extension

A kernel extension could observe:
- Scheduler events (when processes are blocked)
- I/O completion latencies
- Memory pressure at the source

**Why not used:** Kernel extensions are deprecated. System Extensions are complex and require notarization. The ROI doesn't justify the complexity for most users.

### Simpler Approaches

For users who just want to know "what caused my Mac to freeze":

1. **Just use `tailspin`** — Enable it, wait for freeze, save the trace
2. **Check Console.app** — Filter for "hang" or "stall" after a freeze
3. **Activity Monitor > Energy** — Shows which apps have high "Energy Impact"

This daemon adds value through continuous monitoring, historical trending, and automated forensics capture—but simpler tools may suffice for occasional issues.

## Open Questions

Issues identified during review that need resolution:

1. **Stress weight calibration:** Current weights are reasonable starting points for Apple Silicon. Tune based on observed correlation between stress scores and actual pauses after initial deployment.
2. **Suspect patterns vs. correlation:** Hard-coded suspect patterns may create false confidence. Should we remove them in favor of pure correlation-based culprit identification?

### Resolved Questions

- **TUI necessity:** Yes—TUI is the primary interface for real-time monitoring of an active problem.
- **Elevated mode hysteresis:** Yes—elevate at stress>30, de-elevate at stress<20 to prevent rapid cycling. See "Interval Strategy" section.
- **powermetrics restart during stress:** No—always run at 1s intervals, control storage frequency instead. See "Interval Strategy" section.
- **CPU percent collection:** Use simple approach (accept first-sample inaccuracy).
