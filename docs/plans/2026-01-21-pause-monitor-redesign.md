# pause-monitor Design Document

**Date:** 2026-01-21  
**Status:** Draft  
**Purpose:** Capture the intended design for pause-monitor, focusing on what we're building and why.

---

## The Problem

macOS systems (especially Apple Silicon) experience intermittent pauses—moments where the entire system becomes unresponsive. These pauses are difficult to diagnose because:

1. Traditional monitoring tools freeze along with the system
2. By the time you notice, the culprit process may have finished
3. CPU monitors alone miss the real causes (GPU, I/O, thermal, memory pressure)

## The Goal

**Find what causes pauses so you can fix it.**

Not just detect pauses, but capture enough context to identify the responsible process and understand why it caused the problem.

---

## Core Concepts

### Apple Silicon Reality

On Apple Silicon, GPU and CPU share unified memory. GPU utilization is just as important as CPU utilization. A process monopolizing the GPU can freeze the system just as effectively as a CPU hog.

### The Seven Stress Factors

System stress is multi-dimensional. We track seven factors:

| Factor | What It Measures | Why It Matters |
|--------|------------------|----------------|
| **Load** | CPU load relative to core count | Classic overload indicator |
| **Memory** | Memory pressure level | System thrashing, swap activity |
| **Thermal** | Thermal throttling active | CPU/GPU being limited by heat |
| **Latency** | Our own sample timing | System too busy to run our monitor |
| **I/O** | Disk read/write activity | I/O storms can freeze everything |
| **GPU** | GPU utilization | Critical on Apple Silicon |
| **Wakeups** | Idle wake-ups per second | Processes preventing efficient sleep |

A combined stress score (0-100) indicates overall system health. Individual factors point to the cause.

### The Ring Buffer

A 30-second circular buffer in memory that continuously captures system state.

**Why in-memory:** Writing to disk constantly would affect the very stressors we're measuring. The ring buffer lets us observe without interfering.

**Why 30 seconds:** When a pause happens, we need to see what led up to it. 30 seconds of context at 10 samples/second (300 samples) captures the buildup.

### Tier System

Three tiers based on stress score:

| Tier | Name | Stress Range | Meaning |
|------|------|--------------|---------|
| 1 | SENTINEL | 0-14 | Normal operation |
| 2 | ELEVATED | 15-49 | Something is stressing the system |
| 3 | CRITICAL | 50+ | System is likely frozen or about to freeze |

**Hysteresis:** De-escalation requires staying below threshold for 5 seconds to prevent oscillation.

**Note on thresholds:** The values above (15, 50, 5 seconds) are initial estimates, not empirically derived constants. They will be tuned based on real-world use. The ring buffer size (30 seconds) is similarly an initial value balancing context capture against memory use.

---

## Data Collection

### Always Running: powermetrics Stream

The `powermetrics` utility provides comprehensive system data including GPU, thermal state, per-process I/O, and energy impact. It runs continuously as a streaming subprocess.

**Why always running:** Without powermetrics, we're blind to 4 of 7 stress factors (thermal, I/O, GPU, wakeups). We cannot accurately detect stress if we only measure load and memory.

**Sample rate:** 100ms (10Hz). The ring buffer receives complete samples continuously.

**Failure handling:** 
- **Startup:** If powermetrics is unavailable (permission denied, not found), the daemon refuses to start. Fail fast—we cannot accurately detect stress without all 7 factors.
- **Mid-run crash:** Retry with exponential backoff (1s, 2s, 4s, max 30s). Log prominently. Continue sampling what we can from fast-path metrics (load, memory, latency) while retrying.

### Per-Process Metrics

When investigating stress, we need to know WHO is responsible:

| Metric | Why It Matters |
|--------|----------------|
| CPU time/usage | Basic load attribution |
| GPU time/usage | Critical on Apple Silicon |
| Disk I/O (read/write) | Process causing I/O storm |
| Thread count | Thread explosion |
| Idle wake-ups | Process preventing system efficiency |
| Memory (resident, compressed) | Memory hog |
| Energy impact | Apple's composite score |

**Coalition and Responsible PID:** Modern macOS apps spawn helper processes (XPC services). We track coalitions and responsible PIDs to attribute helper activity to the parent app.

---

## Tier Behaviors

### Tier 1: SENTINEL (Monitoring)

- Ring buffer captures continuously
- No database writes
- No special actions
- Just watching

### Tier 2: ELEVATED (Bookmarking)

When stress rises above 15:

- **On entry:** Log timestamp
- **During:** Track peak stress, capture top process at peak
- **On exit:** Log event to database (start time, end time, duration, peak stress, top process)

This is lightweight bookkeeping. No heavy forensics. The goal is trend data: "You had 5 elevated events today, peaked at 35, 42, 38, 41, 45."

**Multiple elevated events:** Just log them all. If a process keeps pushing you to elevated, that pattern is valuable information.

**Coming from Tier 3:** If we de-escalate from Tier 3 to Tier 2, we start a new elevated event (as if coming from Tier 1) but link it to the Tier 3 event. This captures the "recovery period" as part of the overall incident.

### Tier 3: CRITICAL (Frozen)

At stress 50+, the system is likely frozen or severely degraded. We may not even know we're in Tier 3 until we come out of it.

**During long Tier 3 periods:**
- Track peak stress level
- Update peak data every 30 seconds (one full ring buffer cycle)
- This ensures we don't lose the worst moment even if stuck in critical for extended periods

**On exit to Tier 2:** Start a linked elevated event (see Tier 2 section)

### Peak Tracking (Tier 2 and Tier 3)

The ring buffer is ephemeral—only 30 seconds of data. For long elevated or critical periods, we need to preserve peak information.

**Every 30 seconds (one full ring buffer cycle):**
- Check if current peak exceeds stored peak
- Update stored peak stress, top process at peak
- This ensures we capture the worst moment even during hour-long incidents

**Why 30 seconds:** Matches the ring buffer cycle. We're essentially snapshotting the peak before the buffer rolls over.

### Pause Detection

A pause is detected when our 100ms tick takes significantly longer (>2x expected time indicates we were frozen).

**On pause detection:**

1. Freeze the ring buffer (immutable snapshot)
2. Analyze what was changing in the lead-up
3. Identify likely culprit processes
4. Run full forensics (spindump, tailspin, system logs)
5. Log detailed event to database

This is where the real investigation happens.

### Event Linking

Incidents can span multiple tier transitions. Events are linked via a shared `incident_id` column.

**Mechanism:** When an event starts, it generates a new `incident_id`. If transitioning from a higher tier (Tier 3 → Tier 2), the new event inherits the `incident_id` from the previous event, linking them as one incident.

```
Example incident timeline:

Tier 1 → Tier 2    Event A starts (elevated), new incident_id=abc123
Tier 2 → Tier 3    Event A escalates (critical/pause)
[stuck in Tier 3, peaks updated every 30s]
Tier 3 → Tier 2    Event B starts (recovery), inherits incident_id=abc123
Tier 2 → Tier 1    Event B ends
```

Events A and B share `incident_id=abc123`. When reviewing, you see:
- The buildup (Event A: elevated period before the pause)
- The pause/critical period (with peak data)
- The recovery (Event B: elevated period after)

This tells the full story of what happened.

---

## Forensics

### Partial (Elevated Events)

Just the bookmark:
- Start/end timestamps
- Peak stress
- Top process at peak

### Full (Pause Events)

Everything we can capture:
- Ring buffer contents (30 seconds of pre-pause data)
- Process snapshot with all metrics
- spindump (thread stacks)
- tailspin (kernel-level trace)
- System logs filtered for errors/warnings
- Analysis of what was changing (which processes were growing, spiking)

### Culprit Identification

When analyzing a pause, we look for:

- Which process had the biggest increase in CPU/GPU/memory?
- Which process was doing heavy I/O?
- Which process spawned many threads?
- Which process had excessive wake-ups?
- Were multiple processes from the same coalition (app) involved?

The goal is not just "CPU was high" but "Chrome's GPU process was monopolizing the GPU while Spotlight was doing heavy I/O."

---

## User Interfaces

### TUI (Primary)

Real-time dashboard showing:
- Current stress score and breakdown
- Tier status
- Recent events
- Top processes by stress contribution

**Connection:** Unix domain socket to daemon, receives ring buffer data at 10Hz.

**Why socket:** Daemon and TUI are separate processes. The daemon runs in background; TUI is interactive. Socket provides real-time data without disk I/O.

### CLI

- `pause-monitor daemon` — Run the monitor
- `pause-monitor tui` — Launch dashboard
- `pause-monitor status` — Quick health check
- `pause-monitor events` — List pause events
- `pause-monitor history` — Trend analysis

### Database

SQLite for persistence:
- Elevated events (bookmarks)
- Pause events (full details)
- Historical queries for trend analysis

---

## Data Flow Summary

```
powermetrics (streaming)
        │
        ▼
   Stress Calculation (7 factors)
        │
        ▼
   Ring Buffer (30 sec in memory)
        │
        ├──▶ TUI (via Unix socket, 10Hz)
        │
        ├──▶ Tier Manager (escalate/de-escalate)
        │         │
        │         ├── tier2_entry: log start
        │         ├── tier2_exit: log event
        │         └── pause: full forensics
        │
        └──▶ On pause: freeze → analyze → log
```

---

## What This Tool Does NOT Do

- **Predict pauses with certainty** — Elevated stress often precedes pauses, but not always
- **Prevent pauses** — This is diagnostic, not preventive
- **See inside the kernel during a freeze** — We rely on tailspin for that
- **Work during complete system freeze** — We detect pauses after they end

---

## Technical Debt and Replacements

This redesign addresses accumulated technical debt and clarifies which systems we're keeping, replacing, or removing.

### What We're Keeping

| Component | Purpose |
|-----------|---------|
| Sentinel | 100ms tick loop, tier management |
| TierManager | Tier state machine with hysteresis |
| RingBuffer | 30-second in-memory sample storage |
| StressBreakdown | 7-factor stress model |
| Forensics capture | spindump, tailspin, logs on pause |

### What We're Replacing

| Old | New | Reason |
|-----|-----|--------|
| TUI reads SQLite | TUI reads via Unix socket | Real-time 10Hz updates |
| `_slow_loop` (1s timer stub) | powermetrics always streaming | Can't calculate accurate stress without all 7 factors |
| SQLite for real-time display | Socket for real-time, SQLite for history only | Avoid disk I/O during monitoring |

### What We're Removing

| Component | Reason |
|-----------|--------|
| `SamplePolicy` class | Orphaned, replaced by Sentinel tiers |
| `Daemon._run_loop` method | Orphaned, never called |
| `slow_interval_ms` config | No longer applies—powermetrics always runs |
| SQLite writes during Tier 1 | Only write on tier events, not continuously |

### Stubs To Complete (No More TODOs)

| Stub | Current State | Required |
|------|---------------|----------|
| `Sentinel._slow_loop` | Just sleeps | Stream powermetrics, feed stress calculation |
| `collector._get_io_counters` | Returns (0, 0) | Remove—powermetrics provides I/O data |
| `collector._get_network_counters` | Returns (0, 0) | Remove—not needed for stress calculation |
| TUI socket client | Doesn't exist | Connect to daemon, receive ring buffer data |
| Daemon socket server | Doesn't exist | Expose ring buffer to TUI |

### Minimal Goals

For the TUI to be functional:

1. **Daemon produces complete stress data** — All 7 factors populated from powermetrics
2. **Ring buffer receives samples at 10Hz** — Continuous monitoring working
3. **Daemon exposes data via Unix socket** — TUI can connect
4. **TUI displays real-time data at 10Hz** — The "dead fish" is alive
5. **Tier transitions logged to database** — Elevated events tracked for trends
6. **Pause detection triggers forensics** — Core functionality preserved

Not in minimal scope (future work):
- History view in TUI
- `calibrate` CLI command
- Per-process I/O attribution (requires privileged mode)
- All items in `unimplemented_features` memory not listed above

---

## Design Principles

1. **Observe without affecting** — Ring buffer avoids disk writes during monitoring
2. **Complete picture always** — powermetrics runs continuously for all 7 factors
3. **Tiers control actions, not observation** — We always collect the same data; tiers determine what we do with it
4. **Simple elevated tracking** — Just bookmarks, not full forensics
5. **Deep pause investigation** — Full forensics only when it matters
6. **Patterns over snapshots** — Trend data (repeated elevated events) is as valuable as individual pause analysis
7. **Priority when it matters** — Daemon runs at `USER_INITIATED` QoS class to ensure timely sampling during high system load (when monitoring matters most)
