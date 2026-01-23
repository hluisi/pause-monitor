# Phase 6: Cleanup

Part of [pause-monitor Redesign Implementation Plan](2026-01-21-pause-monitor-implementation.md)

---

## CRITICAL: Read This First (For AI Agents)

> **This is a PERSONAL PROJECT — one developer + AI assistants. NO external users. NO backwards compatibility.**

| Principle | What This Means | Anti-Pattern to AVOID |
|-----------|-----------------|----------------------|
| **Delete, don't deprecate** | If code is replaced, DELETE the old code | `@deprecated`, "kept for compatibility" |
| **No dead code** | Superseded code = DELETE it immediately | "might need later", commented-out code |
| **No stubs** | Implement it or don't include it | `return (0, 0)`, `pass`, `NotImplementedError` |
| **No migrations** | Schema changes? Delete the DB file, recreate fresh | `migrate_add_*()`, `ALTER TABLE` |
| **Breaking changes are FREE** | Change anything. No versioning needed. | `_v2` suffixes, compatibility shims |

**Implementation rule:** If old code conflicts with this plan → DELETE IT. If you see migration code → DELETE IT AND USE SCHEMA_VERSION CHECK INSTEAD.

**Database philosophy:** When schema changes, increment `SCHEMA_VERSION`. At startup, if version doesn't match, delete `data.db` and recreate. No migrations. Ever.

---

> **Sub-skill:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Delete orphaned code after the redesign is complete, update documentation.

---

## Summary

4 tasks cleaning up orphaned code and updating docs:
- Task 6.1: Delete Sentinel class, keep only TierManager
- Task 6.1.5: Delete old stress functions
- Task 6.2: Remove SamplePolicy and slow_interval_ms ✅ COMPLETED
- Task 6.3: Remove old Daemon._run_loop ✅ COMPLETED
- Task 6.4: Update memories

---


## Task 6.1: Delete Sentinel Class, Keep Only TierManager

**Files:**
- Modify: `src/pause_monitor/sentinel.py`
- Modify: `tests/test_sentinel.py`
- Modify: Any files importing `Sentinel`

**Step 1: Delete Sentinel class entirely**

Update `src/pause_monitor/sentinel.py`:

1. **Keep these (they're still needed):**
   - `Tier` enum
   - `TierAction` enum  
   - `TierManager` class

2. **DELETE entirely:**
   - `Sentinel` class (all of it)
   - `collect_fast_metrics()` function
   - Any imports only used by deleted code

The file should contain only `Tier`, `TierAction`, and `TierManager`.

**Step 2: Update imports throughout codebase**

Search for `from pause_monitor.sentinel import Sentinel` and remove. The Daemon creates its own TierManager directly.

**Step 2a: Remove IOBaselineManager from daemon.py**

The daemon currently imports and uses `IOBaselineManager` for I/O spike detection:

```python
# DELETE this import
from pause_monitor.stress import IOBaselineManager

# DELETE this line in __init__
self.io_baseline = IOBaselineManager(persisted_baseline=None)
```

The new `_calculate_stress()` method handles I/O directly from powermetrics data, so `IOBaselineManager` is no longer needed.

**Step 3: Delete Sentinel tests**

Update `tests/test_sentinel.py`:
- DELETE all tests that reference `Sentinel` class
- KEEP tests for `TierManager` and `TierAction`
- Rename file to `tests/test_tier_manager.py` if appropriate

**Step 4: Run tests**

Run: `uv run pytest -v`
Expected: All PASS (no references to deleted Sentinel)

**Step 5: Commit**

```bash
git add src/pause_monitor/sentinel.py tests/
git commit -m "refactor: delete Sentinel class, keep TierManager only"
```

---

## Task 6.1.5: Delete Old Stress Functions

**Files:**
- Modify: `src/pause_monitor/stress.py`
- Modify: `tests/test_stress.py`

**Rationale:** The old `calculate_stress()` function and `IOBaselineManager` class are now orphaned. The new architecture uses `Daemon._calculate_stress()` which computes all 8 factors directly from powermetrics data.

**Step 1: Delete from stress.py**

Delete entirely:
- `calculate_stress()` function
- `IOBaselineManager` class
- Any imports only used by deleted code

Keep:
- `StressBreakdown` dataclass (used throughout)
- `MemoryPressureLevel` enum (used by Daemon)
- `get_memory_pressure_fast()` function (used by Daemon)

**Step 2: Delete orphaned tests**

Delete from `tests/test_stress.py`:
- All tests that call `calculate_stress()` directly (approximately 13 tests)
- All tests for `IOBaselineManager`

Keep:
- Tests for `StressBreakdown.total` property
- Tests for `get_memory_pressure_fast()`

**Step 3: Run tests**

```bash
uv run pytest tests/test_stress.py -v
```

**Step 4: Commit**

```bash
git add src/pause_monitor/stress.py tests/test_stress.py
git commit -m "refactor: delete orphaned calculate_stress() and IOBaselineManager"
```

---

## Task 6.2: Remove SamplePolicy and slow_interval_ms ✅ COMPLETED

> **Already done in Cleanup Step 2 (Pre-Implementation).** See "Pre-Implementation Cleanup" section above.
>
> Removed: `SamplePolicy`, `SamplingState`, `PolicyResult`, `_get_io_counters`, `_get_network_counters`, `slow_interval_ms` config.

---

## Task 6.3: Remove Old Daemon._run_loop ✅ COMPLETED

> **Already done in Cleanup Step 1 (Pre-Implementation).** See "Pre-Implementation Cleanup" section above.
>
> Removed: `_run_loop()`, `_collect_sample()`, `_check_for_pause()`, `_handle_pause()`, `_handle_policy_result()`.

---

## Task 6.4: Update Memories

**Files:**
- Modify: `.serena/memories/unimplemented_features.md`
- Modify: `.serena/memories/implementation_guide.md`

**Step 1: Update unimplemented_features**

Mark as completed:
```markdown
## Completed (via Redesign)

- ~~Sentinel slow loop~~ → Replaced by Daemon powermetrics integration
- ~~TUI socket streaming~~ → Implemented via SocketServer/SocketClient
- ~~Complete 8-factor stress (including pageins)~~ → All factors now calculated from powermetrics
- ~~Process attribution~~ → Using powermetrics top_cpu_processes + top_pagein_processes
```

**Step 2: Update implementation_guide**

Document the new architecture:
```markdown
## Architecture (Post-Redesign)

### Data Flow
- Single 100ms loop driven by powermetrics stream
- Ring buffer receives complete samples continuously
- TUI streams from Unix socket (not SQLite polling)
- SQLite stores only tier events (elevated bookmarks, pause forensics)

### Key Components
- `Daemon._main_loop()` - Main 10Hz processing loop
- `Daemon._calculate_stress()` - 8-factor stress from powermetrics
- `TierManager` - Tier state machine (extracted from Sentinel)
- `SocketServer` - Broadcasts ring buffer to TUI
- `SocketClient` - TUI receives real-time data

### Deleted (No Longer Exists)
- `Sentinel` class - Deleted entirely, use `TierManager` directly
- `SamplePolicy` - Deleted
- `slow_interval_ms` config - Deleted
```

**Step 3: Commit**

```bash
git add .serena/memories/
git commit -m "docs: update memories after redesign"
```

---

## Verification Checklist

After completing all tasks:

1. **Daemon runs at 10Hz with complete stress**
   ```bash
   sudo uv run pause-monitor daemon
   # Check logs show samples every ~100ms with GPU, wakeups values
   ```

2. **Socket exists when daemon runs**
   ```bash
   ls -la ~/.local/share/pause-monitor/daemon.sock
   ```

3. **TUI shows "(live)" and updates at 10Hz**
   ```bash
   uv run pause-monitor tui
   # Subtitle should say "System Health Monitor (live)"
   # Stress values should update rapidly
   ```

4. **Tier 2 events create bookmarks with peak_stress**
   ```bash
   # Generate stress, wait for tier 2 exit
   uv run pause-monitor events
   # Should show recent event with peak stress
   ```

5. **All tests pass**
   ```bash
   uv run pytest -v
   ```

6. **Lint passes**
   ```bash
   uv run ruff check . && uv run ruff format --check .
   ```

---

## Summary

| Phase | Tasks | Purpose |
|-------|-------|---------|
| 1 | 1.1–1.9 | Unified Data Model Foundation |
| 2 | 2.1–2.4 | Update PowermetricsStream for 100ms + complete data + failure handling + config |
| 3 | 3.1–3.7 | Refactor Daemon as single loop with tier handling, incident linking, peak tracking |
| 4 | 4.1–4.2 | Add Unix socket server |
| 5 | 5.1–5.2 | Update TUI to use socket client |
| 6 | 6.1–6.4 | Delete orphaned code, update docs |

**Total: 27 tasks** (Tasks 6.2–6.3 already completed in Pre-Implementation Cleanup)

**Key Architecture Changes:**
- powermetrics drives the main loop at 100ms (was 1000ms separate from sentinel)
- Ring buffer receives complete samples (was partial fast-path data)
- TUI streams from socket (was polling SQLite)
- SQLite stores only tier events (was storing all samples)
- Sentinel class deleted, TierManager used directly by Daemon
- Time-based correlation for related events (no explicit incident_id)
- Peak tracking every 30 seconds during elevated/critical periods
- Config-driven thresholds (pause_threshold_ratio, peak_tracking_seconds)
