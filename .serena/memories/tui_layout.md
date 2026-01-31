# TUI Layout Reference

**Last updated:** 2026-01-29

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
│  │ [trend] Process  Score  CPU%  Mem  Pgin  CSW  State  Why    │ │
│  │   ●     Safari     45   12.3  2GB   50   100   run   cpu    │ │
│  │   ▲     Chrome     38    8.1  1GB   20    80   run   mem    │ │
│  │   ...                                                        │ │
│  └─────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────┤
│                       BOTTOM PANELS                              │
│  ┌────────────────────────┐  ┌────────────────────────────────┐ │
│  │     ACTIVITY LOG       │  │     TRACKED PROCESSES          │ │
│  │  (System Activity)     │  │  Time  Process Peak Dur Why St │ │
│  │  [timestamp] message   │  │  12:34 Safari   45  2m  cpu ● │ │
│  │  [timestamp] message   │  │  12:30 Chrome   38  5m  mem ○ │ │
│  └────────────────────────┘  └────────────────────────────────┘ │
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
├── Horizontal (id="bottom-panels")
│   ├── ActivityLog (id="activity")
│   │   ├── Static (id="activity-title")   # "SYSTEM ACTIVITY"
│   │   └── Container (id="log-container") # Scrolling log entries
│   └── TrackedEventsPanel (id="tracked")
│       ├── Static (id="tracked-title")    # "TRACKED PROCESSES"
│       └── DataTable (id="tracked-table") # Tracking history
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
| **Process** | Command name |
| **Score** | Stress score (0-100) |
| **CPU%** | CPU percentage |
| **Mem** | Memory usage (formatted) |
| **Pgin** | Page-ins |
| **CSW** | Context switches |
| **State** | Process state (run/idle/sleep/etc) |
| **Why** | Categories that triggered selection |

**Decay behavior:** Processes that drop out of rogue selection stay visible (dimmed) for a few seconds before disappearing.

---

### 3. Bottom Panels (`id="bottom-panels"`)

Horizontal split containing two panels side by side.

#### 3a. ActivityLog (`id="activity"`)

System event feed showing tier transitions.

| Element | ID | Description |
|---------|-----|-------------|
| **Title** | `#activity-title` | "SYSTEM ACTIVITY" |
| **Log Container** | `#log-container` | Scrolling list of timestamped events |

**Events logged:**
- Tier transitions (entered/exited elevated, high, critical)
- Connection status changes

#### 3b. TrackedEventsPanel (`id="tracked"`)

Processes being tracked (entered elevated+ band).

| Element | ID | Description |
|---------|-----|-------------|
| **Title** | `#tracked-title` | "TRACKED PROCESSES" |
| **Table** | `#tracked-table` | DataTable with tracking history |

**Table columns:**

| Column | Width | Description |
|--------|-------|-------------|
| **Time** | 8 | Entry time (HH:MM:SS) |
| **Process** | auto | Command name (truncated to 15 chars) |
| **Peak** | 4 | Peak score reached |
| **Dur** | 6 | Duration tracked |
| **Why** | auto | Peak categories |
| **Status** | 8 | `[green]active[/]` or `[dim]ended[/]` |

**Tracking logic:**
- Tracks by command name (not PID) to deduplicate
- Active processes shown first, then history
- History limited to 15 entries, sorted by peak score

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
  │   - process_count            │  - ActivityLog.check_transitions()
  │   - timestamp                │  - TrackedEventsPanel.update_tracking()
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
