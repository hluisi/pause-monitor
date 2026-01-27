# Reboot Data Correlation (Future Implementation)

## Problem
PIDs are only valid within a single boot session. On reboot:
- All PID-keyed data becomes stale
- Historical trends by PID become meaningless
- We lose the ability to correlate "chrome was bad yesterday" with "chrome is bad today"

## Proposed Solution
On daemon startup, detect if a reboot occurred (compare `sysctl kern.boottime` with stored boot time).

If reboot detected:
1. **Archive stale PID data** - transform PID-keyed records to command-based format
2. **Create historical archive** - searchable by command name, not PID
3. **Clear PID tables** - `_active` and `_expired` in ProcessTracker

### Archive Format (conceptual)
```python
@dataclass
class ArchivedProcessHistory:
    command: str  # Key field - not PID
    boot_session: str  # UUID or boot timestamp
    first_seen: datetime
    last_seen: datetime
    total_samples_as_rogue: int
    peak_score: int
    # Possibly: score distribution, common categories, etc.
```

### Query Use Cases
- "Show me all historical data for 'chrome' across boot sessions"
- "Which processes have been rogues most frequently?"
- "What was the peak score for 'WindowServer' last week?"

## Dependencies
- ProcessTracker implementation (tracks per-PID data)
- Boot time detection (`sysctl kern.boottime`)
- SQLite table for archived history

## Status
**NOT YET IMPLEMENTED** - captured for future work after ProcessTracker is stable.
