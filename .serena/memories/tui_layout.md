# TUI Layout Reference

**Last updated:** 2026-02-02

---

## Visual Layout

```
┌─────────────────────────────────────────────────────────────────┐
│                         HEADER BAR                               │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ Stress Gauge (gauge-left)      │ Stats (gauge-right)        │ │
│  └─────────────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                     Sparkline                                │ │
│  └─────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────┤
│                       PROCESS TABLE                              │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ [trend] PID Process Score CPU GPU MEM DISK WAKE State Dom   │ │
│  │   ●    1234 Safari    45 10x  2x  5x  0.5x 1.2x  run CPU10x │ │
│  │   ▲    5678 Chrome    38  8x  1x  3x  0.2x 0.8x  run MEM 3x │ │
│  │   ...                                                        │ │
│  └─────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────┤
│                       EVENT HISTORY                              │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ Time     Process         Peak Band     Dur     Status    📸 │ │
│  │ 18:38:37 bridge          56   high     24s     tracking  ✓  │ │
│  │ 18:38:51 2.1.29          47   elevated 12s     ended        │ │
│  │ 18:39:01 ghostty         43   elevated 8s      ended        │ │
│  └─────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────┤
│                          FOOTER                                  │
│  (Textual default footer with keybindings)                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Component Hierarchy

```
RogueHunterApp
├── HeaderBar (id="header")
│   ├── Horizontal
│   │   ├── Label (id="gauge-left")   # Stress gauge + tier + timestamp
│   │   └── Label (id="gauge-right")  # Process count + sample number
│   └── Sparkline (id="sparkline")    # Score history graph
├── ProcessTable (id="main-area")
│   └── ScrollableContainer
│       └── Grid (id="process-grid")  # Header row + data rows
├── EventHistoryPanel (id="event-history")
│   └── DataTable (id="events-table") # Event history from database
└── Footer
```

---

## Component Details

### 1. HeaderBar (`id="header"`)

The status bar at the top showing overall system health.

| Element | ID | Description |
|---------|-----|-------------|
| **Stress Gauge** | `#gauge-left` | `STRESS ████████░░░░ 45/100 ELEVATED 12:34:56` |
| **Stats** | `#gauge-right` | `250 procs #1234` (process count, sample number) |
| **Sparkline** | `#sparkline` | Mini graph showing recent score history |

**Border colors by state:**
- Green: Normal (score < 40)
- Yellow: Elevated (score >= 40)
- Red: Critical (score >= 80)
- Error color: Disconnected

---

### 2. ProcessTable (`id="main-area"`)

The main grid showing current rogue processes.

| Column | Description |
|--------|-------------|
| *(trend)* | `●` stable, `▲` rising, `▽` falling, `○` decayed |
| **PID** | Process ID |
| **Process** | Command name |
| **Score** | Final score (0-100) |
| **CPU** | CPU share (multiple of fair share, e.g., "10.5x") |
| **GPU** | GPU share |
| **MEM** | Memory share |
| **DISK** | Disk I/O share |
| **WAKE** | Wakeups share |
| **State** | Process state (run/idle/sleep/etc) |
| **Dominant** | Highest weighted resource (e.g., "CPU 10.5x") |

**Resource shares:** Values like "10x" mean the process is using 10× its fair share of that resource. Fair share = 1 / active_process_count.

**Decay behavior:** Processes that drop out of rogue selection stay visible (dimmed) for a few seconds before disappearing.

---

### 3. EventHistoryPanel (`id="event-history"`)

Full-width panel showing process events from the database.

| Element | ID | Description |
|---------|-----|-------------|
| **Table** | `#events-table` | DataTable showing event history |

**Table columns:**

| Column | Width | Description |
|--------|-------|-------------|
| **Time** | 8 | Entry time (HH:MM:SS) |
| **Process** | 15 | Command name (truncated) |
| **Peak** | 4 | Peak score reached (colored by band) |
| **Band** | 8 | Peak band (colored) |
| **Dur** | 7 | Duration tracked |
| **Status** | 10 | `[green]tracking[/]` or `[dim]ended[/]` |
| **📸** | 2 | Forensics indicator (✓ if captures exist) |

**Data source:**
- Reads directly from SQLite database (not from socket)
- Queries `process_events` table for recent events
- Queries `get_open_events()` for currently tracked processes
- Checks `forensic_captures` table for forensics indicator
- Refreshes every 10 samples (~3 seconds)

---

### 4. Footer

Standard Textual footer widget showing keybindings (e.g., `q` to quit).

---

## Data Flow

```
Daemon                          TUI
  │                              │
  │──[sample]───────────────────>│  Every sample (~3Hz):
  │   - max_score                │  - HeaderBar.update_from_sample()
  │   - rogues[]                 │  - ProcessTable.update_rogues()
  │   - process_count            │
  │   - timestamp                │
  │                              │
  │                              │  Every 10 samples (~3s):
  │                              │  - EventHistoryPanel.refresh_from_db()
  │                              │    (reads from SQLite directly)
  │                              │
```

**Sparkline**: TUI-managed. Fills to `width: 100%` of header. Maintains its own
180-sample buffer (~60 seconds at 3Hz). Daemon's `initial_state` history is ignored.
Textual's Sparkline aggregates data to fit available width automatically.

---

## CSS Classes

| Class | Applied to | Trigger |
|-------|------------|---------|
| `.elevated` | HeaderBar | score >= 40 |
| `.critical` | HeaderBar | score >= 80 |
| `.disconnected` | HeaderBar, ProcessTable | Lost socket connection |
| `.header` | Grid labels | Column headers |
| `.decayed` | Grid rows | Process no longer in rogue list |

---

## Source File

`src/rogue_hunter/tui/app.py`
