# Per-Process Band Tracking Design

## Y-Statement Summary

**In the context of** tracking which processes cause system stress and when,
**facing** ephemeral ring buffer data that disappears after 30 seconds and no historical record of process behavior,
**we decided for** event-based tracking where crossing a threshold creates an event with captured snapshots,
**to achieve** forensic data for analysis and historical trends across boot sessions,
**accepting** the need to checkpoint during long BAD periods and migrate data on reboot.

## Problem Statement

Current system captures real-time ProcessScore data at 1Hz in a 30-second ring buffer. Data older than 30 seconds is lost. No way to answer:
- "What happened when chrome was stressed for 5 minutes?"
- "Which processes have been problematic this week?"
- "What did the system look like when this process peaked?"

If we do nothing:
- TUI shows ephemeral snapshots that disappear before users can read them
- Forensics captures lack context about process history
- No way to identify repeat offender processes
- Valuable diagnostic data lost after 30 seconds

## Goals

**Must have:**
- Track when processes cross the "bad" threshold (configurable score)
- Capture full ProcessScore snapshots at entry, peak, exit, and checkpoints
- Persist all forensic data — nothing thrown away once captured
- Support trend queries by command name across reboots
- One data schema throughout (ProcessScore)

**Should have:**
- Configurable threshold for what constitutes "bad"
- Configurable band labels for descriptive scoring
- Checkpoint frequency tied to ring buffer size

## Non-Goals

| Non-Goal | Reason |
|----------|--------|
| TUI changes to display new data | Focus on daemon/data layer first |
| CLI query commands for history | Later, once data layer is stable |
| Notifications based on new system | Existing system works for now |
| Backwards compatibility with old tier system | Replace, don't wrap |
| Multiple threshold levels with different behaviors | Simplicity — one threshold, binary state |

## Proposed Approach

### Two States (Not Five)

The five bands (low, medium, elevated, high, critical) describe **how bad** a score is. But the action is binary:

| State | Behavior |
|-------|----------|
| **NORMAL** | Score below threshold. Track minimally. Ring buffer has it if needed. No persistence. |
| **BAD** | Score at or above threshold. Create event, capture snapshots, persist when complete. |

Bands are **descriptive labels** on scores, not different states with different behaviors.

### Event-Based Tracking

When a process crosses the threshold:
1. Create an EVENT (unique ID ties all snapshots together)
2. Capture entry snapshot
3. Track peak (replace snapshot when new peak reached)
4. Checkpoint every ring buffer cycle
5. Capture exit snapshot when it leaves BAD state
6. Event is complete

### Data Flow

```
Ring buffer (real-time, 30s window)
         │
         │ Score crosses threshold
         ▼
Create EVENT (process_events row)
  - Capture entry snapshot
  - Initialize peak snapshot
         │
         │ While BAD
         ▼
Checkpoint every ring buffer cycle
  - Add snapshot to process_snapshots
  - Update peak if new high reached
         │
         │ Score drops below threshold OR falls off selection
         ▼
Close EVENT
  - Capture exit snapshot
  - Set ended_at
         │
         │ System reboots
         ▼
PID meaningless, query by command name
```

### Single Data Schema

ProcessScore is THE schema. It's what's in:
- Ring buffer (real-time)
- Entry snapshot
- Peak snapshot
- Checkpoint snapshots
- Exit snapshot

No new data structures. Just ProcessScore captured at different moments with metadata about when/why.

## Schema

```sql
CREATE TABLE process_events (
    id INTEGER PRIMARY KEY,
    pid INTEGER NOT NULL,
    command TEXT NOT NULL,
    boot_time INTEGER NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,                    -- NULL if still active
    peak_score INTEGER NOT NULL,
    peak_snapshot TEXT NOT NULL,      -- JSON: ProcessScore at peak
    peak_captured_at REAL NOT NULL
);

CREATE TABLE process_snapshots (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL,
    captured_at REAL NOT NULL,
    capture_type TEXT NOT NULL,       -- 'entry', 'checkpoint', 'exit'
    snapshot TEXT NOT NULL,           -- JSON: ProcessScore
    FOREIGN KEY (event_id) REFERENCES process_events(id)
);
```

### What's Captured Where

| What | Where | When updated |
|------|-------|--------------|
| Entry snapshot | process_snapshots | Once, when event created |
| Peak snapshot | process_events row | Replaced when new peak reached |
| Checkpoint snapshots | process_snapshots | Added every ring buffer cycle |
| Exit snapshot | process_snapshots | Once, when event closes |

### Reboot Handling

- Store `boot_time` (from `sysctl kern.boottime`) in daemon_state
- On daemon startup: compare stored vs current boot_time
- If different: reboot happened, PIDs are stale
- Events remain queryable by command name (PID ignored for historical queries)

## Triggers

| Trigger | Action |
|---------|--------|
| Score crosses threshold (enters BAD) | Create event, capture entry snapshot, initialize peak |
| New peak score while BAD | Update peak_score, peak_snapshot, peak_captured_at on event |
| Ring buffer cycle while BAD | Add checkpoint snapshot |
| Score drops below threshold (exits BAD) | Add exit snapshot, set ended_at |
| PID falls off selection while BAD | Add exit snapshot, set ended_at |
| Daemon startup | Check boot_time, update if rebooted |

## Configuration

```toml
[bands]
# Descriptive labels for scores (metadata only)
low = 20        # 0-19 = low
medium = 40     # 20-39 = medium
elevated = 60   # 40-59 = elevated
high = 80       # 60-79 = high
critical = 100  # 80-100 = critical

# The threshold that triggers BAD state and persistence
threshold = 40
```

## Alternatives Considered

| Option | Pros | Cons | Why Rejected |
|--------|------|------|--------------|
| Store every sample | Complete data | Storage bloat, redundant data | Wasteful |
| Store periodic samples (every Ns) | Predictable storage | Arbitrary interval, misses transitions | Doesn't capture what matters |
| Five states with different behaviors | Granular control | Complexity without benefit | Actions are binary (persist or not) |
| Keep tier system alongside bands | Less disruptive | Two overlapping systems | Contradicts "replace don't wrap" |
| Peak as separate snapshot row | All snapshots in one table | Delete+insert instead of update | Awkward for replacement semantics |

## Consequences & Trade-offs

**Positive:**
- Complete forensic data for every BAD period
- Self-contained events — each tells a full story
- Same schema everywhere (ProcessScore)
- Trend queries by command survive reboots
- Clean binary model (NORMAL vs BAD)

**Negative:**
- Breaking change — old tier code must be removed
- Schema migration required
- Tests must be rewritten

**Neutral:**
- Checkpoint frequency tied to ring buffer size

## Principles (Implementation Constraints)

| Principle | What it means |
|-----------|---------------|
| **Replace, don't wrap** | Remove TierManager if bands replace tiers |
| **Delete dead code** | Old tier-related code, unused tables, orphaned tests — delete them |
| **No backwards compatibility** | Breaking changes are fine |
| **No stubs** | If we can't implement it, don't write the signature |
| **Tests follow code** | Delete old tests, write new ones |
| **One schema** | ProcessScore everywhere |

## Success Criteria

| Criteria | How to verify |
|----------|---------------|
| Process below threshold → no persistence | Observe logs, query DB — no events for low-score PIDs |
| Process crosses threshold → event created | Query process_events — row exists with entry snapshot |
| Peak tracked correctly | Trigger multiple peaks, verify only highest is stored |
| Long BAD period → checkpoints captured | Process BAD for 2+ minutes, verify checkpoint rows |
| Exit captured | Query process_snapshots — exit row exists when event closes |
| Query by command after reboot | Simulate reboot, query by command — data accessible |
| No data thrown away | All forensic data preserved from BAD periods |

## Open Questions

| Question | Resolution |
|----------|------------|
| In-memory tracking structure? | Defer to implementation plan |
| Migration from old events table? | Defer to implementation plan |
| Index strategy for queries? | Defer to implementation plan |
