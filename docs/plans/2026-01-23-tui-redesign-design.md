# TUI Redesign Design

## Y-Statement Summary

In the context of **real-time system monitoring**, facing **ephemeral process data that disappears before it can be read** and **a cluttered multi-screen interface**, we decided for **a single dense btop-style screen with per-process stress scoring and a persistent activity log** to achieve **immediate visibility into what's causing system stress**, accepting **less screen space for historical event management (delegated to CLI)**.

## Problem Statement

The current TUI has evolved through multiple daemon redesigns and no longer serves its primary purpose well:

1. **Data visibility is fragmented** — Separate "top CPU" and "top pageins" tables split the picture of what a single process is doing
2. **Ephemeral data disappears** — A process causing 800 pageins/s for 2 seconds flashes by before you can read it
3. **No meaningful sorting** — Process tables show "top by X metric" but can't answer "which process is the worst overall offender"
4. **Over-complicated navigation** — Separate screens for events, detail views, and history when the primary use case is "watch what's happening now"
5. **Activity context is lost** — No log of what happened moments ago; you only see the current instant

The daemon now collects rich data (8-factor stress, per-process metrics for CPU/pageins/wakeups/IO) but the TUI doesn't surface it effectively.

## Goals

1. **Single-screen real-time monitoring** — Everything visible at once, btop-style, no page switching for primary use case
2. **Unified process view** — One table showing all metrics per process, sorted by "worst offender"
3. **Persistent activity log** — Capture threshold crossings and tier changes so ephemeral spikes are recorded
4. **Per-process stress scoring** — New feature: calculate stress score for each process using same scaling as system stress
5. **Tier-adaptive detail** — Normal operation shows recent activity; elevated tiers automatically show full 30s context
6. **Visual hierarchy** — Most important information (process table, activity log) gets most space; secondary info (sparkline, raw metrics) is compact

## Non-Goals

1. **Event triage workflow** — The CLI (`pause-monitor events`) handles reviewing/marking events; TUI just shows a compact "recent events" widget
2. **Historical trend analysis** — Out of scope; future feature (history view stub remains)
3. **Configurability of layout** — Fixed layout optimized for the primary use case
4. **Mobile/small terminal support** — Designed for standard terminal sizes (80x24 minimum, optimized for larger)

## Proposed Approach

### Layout Structure

```
┌─ pause-monitor ─────────────────────────────────────────────────────────────┐
│ STRESS ████████████░░░░░░░░  42/100  [TIER 1]              14:32:07        │
├─────────────────────────────────────────────────────────────────────────────┤
│ ▁▂▃▄▃▂▂▃▅▆▅▄▃▂▂▃▄▅▆▇█▇▆▅▄▃▂▁▁▂▃ (30s)   Load:2.1 Mem:72% Pgin:12/s IO:45M │
├─ PROCESSES (sorted by stress) ──────────────────────────────────────────────┤
│ NAME                 STRESS   CPU ms/s   PAGEINS/s   IO KB/s   WAKEUPS/s   │
│ Chrome                  47      1842          12       2048        145     │
│ mds_stores              23       412           0       4096         23     │
│ kernel_task             18       523           0        128        892     │
│ WindowServer            12       387           0         12        456     │
│ Code Helper              8       298           3        512         67     │
│ ...                                                                        │
├─ ACTIVITY LOG ──────────────────────────────────────────────────────────────┤
│ 14:32:05  ▲ Chrome           847 pageins/s                                 │
│ 14:31:58  ▲ mds_stores       12.4 MB/s IO                                  │
│ 14:31:42  ● Tier → ELEVATED  (stress: 67)                                  │
│ 14:31:40  ▲ kernel_task      2100 CPU ms/s                                 │
│ 14:31:35  ● Tier → NORMAL    (stress: 14)                                  │
│ 14:31:22  ▲ Spotlight        340 pageins/s                                 │
├─ RECENT EVENTS ─────────────────────────────────────────────────────────────┤
│ ○ Jan 23 14:28  32s  peak:72   ○ Jan 23 12:15  8s  peak:54   [e] more     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Component Hierarchy

**Primary (most screen space):**
- **Process Table** — Unified view with per-process stress score, all four metrics (CPU, pageins, I/O, wakeups), sorted by stress
- **Activity Log** — Scrolling log of threshold crossings and tier changes, 6-8 visible lines

**Secondary (visible but compact):**
- **Stress Sparkline** — Visual 30-second history graph
- **Stress Score + Tier** — Large, glanceable current state

**Tertiary (single line, compact):**
- **Raw Metrics** — Load avg, memory %, pageins/s, I/O rate
- **Recent Events Widget** — Horizontal compact list of last 2-3 events from database

### Per-Process Stress Score (New Feature)

Apply the same scaling logic used for system stress to individual processes:

| Factor | Max Points | Threshold Logic |
|--------|------------|-----------------|
| CPU | 0-30 | Based on `cpu_ms_per_s`: <500=0, 500-2000 scales to 15, 2000+ scales to 30 |
| Pageins | 0-30 | Based on `pageins_per_s`: <10=0, 10-100 scales to 15, 100+ scales to 30 |
| I/O | 0-10 | Based on `diskio_per_s`: <10MB/s=0, 10-100MB/s scales to 10 |
| Wakeups | 0-10 | Based on `wakeups_per_s`: <100=0, 100-500 scales to 10 |

**Total per-process stress: 0-80**

This score:
1. Sorts the process table (worst offender at top)
2. Triggers activity log entries when crossing thresholds
3. Provides consistent mental model with system stress

### Activity Log Behavior

**Triggers (emit log entry when):**
- Process crosses HIGH threshold: >100 pageins/s, >2000 cpu_ms/s, >50MB/s IO, >500 wakeups/s
- Tier transitions: NORMAL↔ELEVATED↔CRITICAL
- System stress crosses 25-point boundaries (25, 50, 75)

**Tier-adaptive depth:**
- **Tier 1 (Normal):** Show last ~10 seconds of activity — recent context only
- **Tier 2+ (Elevated/Critical):** Expand to show full 30-second buffer — forensic context

**Persistence:**
- Entries stay in the log until they scroll off (memory buffer)
- Log survives across tier transitions (accumulated history)

### Process Table Behavior

**Decay-based persistence:**
- Process stays visible for 5 seconds after last significant activity
- Visual fade (dimmed text) indicates "was active, now quiet"
- Prevents the "blink and you miss it" problem

**Columns:**
- NAME (truncated to ~20 chars)
- STRESS (per-process score, 0-80)
- CPU ms/s
- PAGEINS/s
- IO KB/s
- WAKEUPS/s

**Row count:** Fill available terminal height (typically 8-15 processes)

### Removed Complexity

| Removed | Replacement |
|---------|-------------|
| EventsScreen (full-page modal) | Compact "Recent Events" widget + CLI for triage |
| EventDetailScreen | CLI `pause-monitor events <id>` |
| HistoryScreen | Stub notification remains; future feature |
| StressBreakdown panel | Folded into raw metrics line |
| Separate CPU/Pagein tables | Unified process table |

### Keybindings

| Key | Action |
|-----|--------|
| q | Quit |
| r | Refresh (force redraw) |
| e | Show events in CLI (spawn `pause-monitor events` or show notification) |
| ↑/↓ | Scroll activity log (when log is focused) |
| ? | Show help overlay |

## Alternatives Considered

| Option | Pros | Cons | Why Rejected |
|--------|------|------|--------------|
| **Keep multi-screen design** | Familiar, more space per view | Context switching loses real-time awareness | Primary use case is real-time monitoring |
| **Factor-centric view** (show factors, then processes per factor) | Clear mapping of "what's causing memory stress" | Fragments process picture; same process appears multiple times | Process-centric is more actionable |
| **Log-only process view** (no table, just activity stream) | Everything is persistent | Loses "current state" overview; have to read backwards | Need both instant state and history |
| **Configurable panel sizes** | User flexibility | Complexity; most users want sensible defaults | YAGNI; can add later if requested |

## Consequences & Trade-offs

**Positive:**
- Immediate visibility into worst offenders via per-process stress sorting
- Ephemeral spikes captured in activity log for later review
- Simpler mental model: one screen, process-centric
- Consistent scoring model (system and process use same scales)

**Negative:**
- Less space for event triage (delegated to CLI)
- Fixed layout may not suit all terminal sizes
- Activity log thresholds are hardcoded initially

**Neutral:**
- Per-process stress is computed client-side (TUI) not daemon-side — keeps daemon protocol stable
- Recent events widget queries database periodically (existing pattern)

## Success Criteria

1. **No page switching during normal monitoring** — User can see everything without pressing navigation keys
2. **Process table always shows worst offender at top** — Per-process stress sorting works
3. **Brief spikes are captured** — A 2-second pagein spike appears in activity log and is readable
4. **Tier escalation provides context** — Entering Tier 2 automatically shows 30s of history in log
5. **Simpler than before** — Fewer widgets, screens, and keybindings than current TUI

## Open Questions

1. **Activity log threshold tuning** — Should thresholds be configurable, or are sensible defaults sufficient? (Recommendation: defaults first, config later if needed)
2. **Per-process stress in daemon vs TUI** — Should daemon compute and broadcast per-process stress, or should TUI compute from raw metrics? (Recommendation: TUI computes, keeps daemon stable)
3. **Sparkline library** — Textual has built-in Sparkline widget; need to verify it supports our use case
4. **Terminal size handling** — What's minimum usable size? What degrades gracefully?
