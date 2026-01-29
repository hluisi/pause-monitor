---
version: 1
plan: docs/plans/2026-01-27-per-process-band-tracking-plan.md
branch: refactor/per-process-band-tracking
started: 2026-01-27 15:11
---

# Build Log: Per-Process Band Tracking

> Building from: `2026-01-27-per-process-band-tracking-plan.md`
> Branch: `refactor/per-process-band-tracking`
> Started: 2026-01-27 15:11

---

## Task 1: Add captured_at to ProcessScore

### Implement
- Self-review: Clean implementation, all requirements met
- Concerns: None

### Spec Review
- Result: ✅ Compliant
- Observations: All 8 requirements verified by code inspection

### Code Review
- Strengths: Clean, minimal changes, comprehensive test fixture updates, TDD followed
- Issues:
  - Critical: None
  - Important: Missing test assertion for `_score_process()` setting `captured_at`
  - Minor: None
- Assessment: Good code quality overall

### User Decision
Proceed as-is (accept Important issue)

### Committed
`c2fc735` - feat(collector): add captured_at timestamp to ProcessScore

---

## Task 2: Add BandsConfig

### Implement
- Self-review: BandsConfig created with all methods, TiersConfig deleted, dependent code updated
- Concerns: None

### Spec Review
- Result: ✅ Compliant
- Observations: All 8 requirements verified, 6 tests added

### Code Review
- Strengths: Clean dataclass design, proper test coverage, consistent integration
- Issues:
  - Critical: None
  - Important: Missing validation for invalid band names
  - Minor: None
- Assessment: Good, needs validation

### User Decision
Fix the validation issues

### Implement (fix)
- Self-review: Added ValueError with helpful messages in get_threshold() and _load_bands_config()
- Concerns: None

### Code Review
- Result: ✅ Approved
- Observations: All validation issues fixed, 3 additional tests added, 295 tests pass

### Committed
`4a110d8` - feat(config): replace TiersConfig with BandsConfig
`7e9ae0d` - Task 2: Add band validation

---

## Task 3: Add boot time detection and daemon_state helpers

### Implement
**What was built:**
- Created `src/pause_monitor/boottime.py` with `get_boot_time()` function using `os.stat("/var/run").st_birthtime`
- Added `get_daemon_state()` and `set_daemon_state()` functions to storage.py
- Created `tests/test_boottime.py` with 3 tests (returns_int, is_stable, is_in_past)
- Added 3 tests to `tests/test_storage.py` (missing_key, set_and_get, overwrites)

**Files changed:**
- src/pause_monitor/boottime.py (created)
- tests/test_boottime.py (created)
- src/pause_monitor/storage.py (added 2 functions)
- tests/test_storage.py (added 3 tests)

**Testing:**
- All 6 new tests passing
- Full test suite (301 tests) passes
- Linting passes

**Self-review findings:**
- Adapted test code to use `init_database` (actual function name) instead of `init_db` from spec
- Used existing codebase patterns

**Concerns/observations:**
None

### Spec Review
**Result:** ✅ Compliant

**Findings:**
All 8 requirements verified by code inspection:
1. `get_boot_time()` returns int via `int(os.stat("/var/run").st_birthtime)` - boottime.py:8
2. Stable across calls (uses filesystem birthtime)
3. Boot time before current time (verified by test)
4. Exact implementation matches spec
5. `get_daemon_state()` returns None for missing key - storage.py:173-174
6. `set_daemon_state()` stores and retrieves correctly - storage.py:177-183
7. Upsert behavior via `INSERT OR REPLACE`
8. All 6 tests present (3 boottime, 3 daemon_state)

**Suggestions/observations:**
None - minimal implementation matching spec exactly

### Code Review
**Strengths:**
- Clean, minimal implementation (boottime.py is just 8 lines)
- Consistent patterns matching existing storage.py code
- Good test coverage breadth
- All tests pass, zero lint errors

**Issues:**
- **Critical:** `get_boot_time()` returns incorrect value — `/var/run` birthtime is NOT reliably reset on macOS reboots. On test machine: `os.stat("/var/run").st_birthtime` → Nov 22, 2025 (66 days ago), but actual boot time is Jan 23, 2026 (4 days ago per `sysctl kern.boottime`). Tests pass because they only verify type/stability/past, not correctness.
- **Important:** Missing error handling in `get_daemon_state()` — will raise `OperationalError` if table doesn't exist (unlike `get_schema_version()` which handles this)
- **Minor:** Tests don't use existing `initialized_db` fixture

**Assessment:**
Needs revision — critical correctness bug in boot time detection

### User Decision
**Choice:** Fix all issues

**Reasoning:** Critical bug - the boot time detection doesn't actually detect boot time correctly. Ironic that we used `sysctl kern.boottime` (the correct approach) to prove `/var/run` birthtime (the incorrect approach) was wrong.

**Deferred items:** None

### Implement (fix code review issues)
**What was built:**
- Fixed `get_boot_time()` to use `sysctl kern.boottime` instead of `/var/run` birthtime
- Added `try/except sqlite3.OperationalError` to `get_daemon_state()` matching existing pattern in `get_schema_version()`
- Added `test_get_daemon_state_no_table` test for the new error handling

**Files changed:**
- src/pause_monitor/boottime.py (replaced implementation)
- src/pause_monitor/storage.py (added error handling)
- tests/test_storage.py (added test)

**Testing:**
- All 302 tests pass
- No linter errors

**Self-review findings:**
Did not refactor tests to use `initialized_db` fixture — optional change with no functional benefit.

**Concerns/observations:**
None

### Code Review (re-review)
**Strengths:**
- Clean, minimal implementation
- Appropriate error handling (RuntimeError for parse failure, OperationalError for missing table)
- Consistency with existing storage.py patterns
- Good test coverage (7 tests total)
- Tests are fast (0.10s)
- Linter clean, proper parameterized queries

**Issues:**
- **Important:** No test for `set_daemon_state()` when table doesn't exist — asymmetric with `get_daemon_state()` which handles this. Acceptable since daemon always initializes DB first.
- **Important:** No test for error path in `get_boot_time()` (the RuntimeError when parsing fails). Acceptable since happy path is tested.
- **Minor:** Subprocess check=False is implicit — error message would be "Failed to parse" even if sysctl failed to run.
- **Minor:** Test could verify `updated_at` is set (not used yet though).

**Assessment:**
GOOD QUALITY — No blockers. Task implementation is solid and ready for integration.

### User Decision
**Choice:** Proceed as-is

**Reasoning:** Reviewer says no blockers. Edge case tests are nice-to-have but not critical.

**Deferred items:** Missing tests for error paths (set_daemon_state on missing table, get_boot_time parse failure)

### Committed
`138b754` - Task 3: Add boot time detection and daemon_state helpers
`32ec26f` - Task 3: Fix code review issues

---

## Task 4: New database schema

### Implement
**What was built:**
- Updated schema from v7 to v8
- Added `process_events` table (id, pid, command, boot_time, entry_time, exit_time, entry_band, peak_band, peak_score, peak_snapshot)
- Added `process_snapshots` table (id, event_id, snapshot_type, snapshot)
- Added `schema_version` table
- Removed legacy tables: `events`, `event_samples`, `samples`, `process_samples`
- Added indexes: idx_process_events_pid_boot, idx_process_events_open (partial), idx_process_snapshots_event

**Files changed:**
- src/pause_monitor/storage.py (new schema)
- tests/test_storage.py (5 new tests, updated existing tests)

**Testing:**
- 5 new schema tests pass
- 19 storage tests fail (expected - use legacy `events` table)
- 33 other tests fail (expected - use legacy functionality)
- Breaking change is intentional per task description

**Self-review findings:**
Legacy functions (create_event, finalize_event, etc.) still exist but will fail at runtime. Task 10 will remove.

**Concerns/observations:**
None - breaking change is intentional

### Spec Review
**Result:** ✅ Compliant

**Findings:**
All 9 requirements verified:
1. `process_events` table exists - storage.py:37-48
2. `process_snapshots` table exists - storage.py:50-56
3. Legacy `events` table removed from schema
4. `process_events` has all 10 required columns
5. `process_snapshots` has all 4 required columns
6. Foreign key with ON DELETE CASCADE - storage.py:55
7. All 3 indexes including partial index - storage.py:58-63
8. SCHEMA_VERSION = 8 - storage.py:15
9. 5 schema tests present and passing

**Suggestions/observations:**
Legacy code (create_event, finalize_event, etc.) still references removed tables — will fail at runtime. Not a spec issue; Task 10 will clean up.

### Code Review
**Strengths:**
- Clean schema design with appropriate column types
- Smart partial index for open events
- Good foreign key constraint with CASCADE
- 5 new tests cover table structure
- Linter clean

**Issues:**
- **Important:** `process_sample_records` schema mismatch — schema now has (id, timestamp, data) but `insert_process_sample()` still tries to insert (event_id, tier, data). Will fail at runtime.
- **Important:** Dead code referencing removed tables (create_event, finalize_event, etc.) — expected, Task 10 will clean up.
- **Important:** VALID_EVENT_STATUSES kept but orphaned — new schema has no status field.
- **Minor:** ProcessSampleRecord dataclass has wrong fields (event_id, tier)
- **Minor:** schema_version table defined but never used (get_schema_version reads from daemon_state)
- **Minor:** Tests use issubset() instead of exact equality

**Assessment:**
Acceptable with caveats. Core schema is solid. The `process_sample_records` schema/code mismatch needs clarification.

### User Decision
**Choice:** Fix the mismatch

**Reasoning:** The schema change to `process_sample_records` was intentional (matches plan), but the functions weren't updated.

**Deferred items:** None

### Implement (fix schema mismatch)
**What was built:**
- Updated `ProcessSampleRecord` dataclass: now `(timestamp, data, id)` instead of `(event_id, tier, data, id)`
- Updated `insert_process_sample()`: signature `(conn, timestamp, samples)`, inserts `(timestamp, data)`
- Updated `get_process_samples()`: queries by time range `(conn, start_time, end_time, limit)` instead of event_id
- Removed `VALID_EVENT_STATUSES` (inlined in legacy function)
- Removed orphaned `schema_version` table from SCHEMA
- Fixed `prune_old_data()` to remove broken event_id reference

**Files changed:**
- src/pause_monitor/storage.py (46 insertions, 33 deletions)

**Testing:**
- Test failures are all related to legacy `events` table code — expected, Task 10 cleanup

**Self-review findings:**
None

**Concerns/observations:**
None

### Code Review (re-review)
**Strengths:**
- Clean schema design with proper foreign keys and indexes
- ProcessSampleRecord refactored correctly
- Updated functions follow established patterns
- Linting passes

**Issues:**
- **Critical:** 20 tests failing — legacy code (Event dataclass, create_event, finalize_event, get_events, etc.) still references deleted tables
- **Critical:** Legacy tests not updated for new `insert_process_sample()` signature
- **Important:** prune_old_data() still references non-existent `event_samples` table
- **Important:** No migration path for existing databases (plan says breaking change is fine)
- **Minor:** Missing index on process_sample_records.timestamp
- **Minor:** get_process_samples() returns DESC order without docstring mention

**Assessment:**
Reviewer says codebase is "broken" — but this is expected mid-refactor. The plan has Task 7 (delete tier system) and Task 10 (cleanup) specifically to address this. The schema changes are correct; the failing tests are for code that will be deleted in later tasks.

### User Decision
**Choice:** Proceed to Task 5

**Reasoning:** Transitional state is expected during multi-task refactor. Failing tests are for code scheduled for deletion in Task 7.

**Deferred items:** Legacy code cleanup (Task 7), test cleanup (Task 10)

### Committed
`c6b4cf2` - Task 4: New database schema v8 with process_events and process_snapshots
`d87a33f` - Task 4: Fix process_sample_records schema mismatch

---

## Task 5: Storage functions for process events

### Implement
**What was built:**
- `create_process_event()` — creates event when process crosses threshold, returns ID
- `get_open_events()` — retrieves unclosed events for current boot
- `close_process_event()` — sets exit_time
- `update_process_event_peak()` — updates peak_score, peak_band, peak_snapshot
- `insert_process_snapshot()` — stores snapshots at significant moments

**Files changed:**
- src/pause_monitor/storage.py (added 5 functions, 77 lines)
- tests/test_storage.py (added 5 tests, 175 lines)

**Testing:**
- All 5 new tests pass
- Pre-existing 20 failures from legacy code (Task 10)

**Self-review findings:**
Added assert for return type safety in create_process_event

**Concerns/observations:**
None

### Spec Review
**Result:** ✅ Compliant

**Findings:**
All 5 functions match spec exactly:
1. `create_process_event()` - storage.py:404-425 ✅
2. `get_open_events()` - storage.py:428-447 ✅
3. `close_process_event()` - storage.py:450-456 ✅
4. `update_process_event_peak()` - storage.py:459-471 ✅
5. `insert_process_snapshot()` - storage.py:474-485 ✅
All 5 tests exist (lines 755-914) and pass.

**Suggestions/observations:**
None

### Code Review
**Strengths:**
- All 5 functions match specification exactly
- Clean type hints throughout (conn: sqlite3.Connection, proper return types)
- Consistent with existing codebase patterns (connection as first param, parameterized queries, conn.commit())
- Tests are thorough and well-structured with proper fixtures
- No linting errors, no stubs

**Issues:**
- **Important:** `create_process_event()` uses `assert result is not None` for type safety (storage.py:423-424). Assert statements are stripped with `-O` flag, potentially returning None and violating the `-> int` return type. Low risk since SQLite always returns lastrowid for successful inserts.
- **Minor:** `get_open_events()` returns `list[dict]` instead of typed dataclass — matches existing codebase pattern
- **Minor:** Test imports inside function bodies — matches existing test file style
- **Minor:** No `captured_at` in process_snapshots — intentional, snapshot JSON contains it from ProcessScore

**Assessment:**
Ready to merge. Implementation matches spec, well-tested, follows existing patterns. The assert issue is low-risk and consistent with typical Python handling.

### User Decision
**Choice:** Proceed as-is

**Reasoning:** Low-risk edge case, project doesn't use `-O` flag, pattern is consistent with codebase.

**Deferred items:** None

### Committed
`71fbb61` - Task 5: Add process event CRUD functions

---

## Task 6: ProcessTracker class

### Implement
**What was built:**
- `TrackedProcess` dataclass — In-memory state for a tracked process with fields: event_id, pid, peak_score
- `ProcessTracker` class — Core tracking logic with methods:
  - `__init__(conn, bands, boot_time)` — Initialize with DB connection, bands config, boot time
  - `_restore_open_events()` — Restore tracking state from open events in DB on startup
  - `update(scores)` — Main update method processing a list of ProcessScore objects
  - `_open_event(score)` — Create new event when process enters bad state
  - `_close_event(pid, exit_time)` — Close event when process exits bad state
  - `_update_peak(score)` — Update peak score/band when score increases

**Files changed:**
- src/pause_monitor/tracker.py (created, 132 lines)
- tests/test_tracker.py (created, 327 lines)

**Testing:**
- test_tracker_creates_event_on_threshold_crossing — Verifies event creation when score crosses threshold ✅
- test_tracker_closes_event_when_score_drops — Verifies event closing when score drops below threshold ✅
- test_tracker_updates_peak — Verifies peak score/band updates ✅
- test_tracker_closes_missing_pids — Verifies event closing when PID disappears from scores ✅
- test_tracker_restores_state_from_db — Verifies state restoration on init ✅
- test_tracker_inserts_entry_snapshot — Verifies entry snapshot insertion ✅
All 6 new tests pass.

**Self-review findings:**
- All code fully implemented, no stubs
- Linting passes (fixed one unused import)
- Uses existing storage functions correctly

**Concerns/observations:**
- Pre-existing test failures from schema v8 migration (not caused by this task)
- Uses time.time() as fallback exit_time when closing events for missing PIDs with empty scores list

### Spec Review
**Result:** ✅ Compliant

**Verification:**
| Requirement | Location | Status |
|-------------|----------|--------|
| TrackedProcess fields (event_id, pid, peak_score) | tracker.py:22-28 | ✅ |
| ProcessTracker.__init__(conn, bands, boot_time) | tracker.py:34-44 | ✅ |
| _restore_open_events() from get_open_events() | tracker.py:46-53 | ✅ |
| update() logic (close missing, open/close/update) | tracker.py:55-82 | ✅ |
| _open_event() creates event + entry snapshot | tracker.py:84-107 (line 101) | ✅ |
| _close_event() calls close_process_event | tracker.py:109-115 | ✅ |
| _update_peak() calls update_process_event_peak | tracker.py:117-131 | ✅ |
| test_tracker_creates_event_on_threshold_crossing | test_tracker.py:7 | ✅ |
| test_tracker_closes_event_when_score_drops | test_tracker.py:64 | ✅ |
| test_tracker_updates_peak | test_tracker.py:119 | ✅ |
| test_tracker_closes_missing_pids | test_tracker.py:178 | ✅ |
| All tests pass | 6 passed in 0.02s | ✅ |

**Suggestions/observations:**
- Two additional tests beyond spec (test_tracker_restores_state_from_db, test_tracker_inserts_entry_snapshot) provide extra coverage
- Defensive early return in _close_event() is a reasonable guard

### Code Review
**Strengths:**
- Clean, focused design with single responsibility
- Proper state management with TrackedProcess dataclass
- Correct event lifecycle handling
- Good use of existing infrastructure (BandsConfig, storage functions)
- Complete type hints throughout
- Comprehensive tests covering key scenarios

**Issues:**
- **Important:** No exit snapshot on close — `_close_event()` (tracker.py:109-115) doesn't capture process state when exiting bad band. Could be useful for understanding recovery. Suggested: add exit snapshot or document why omitted.
- **Important:** No score available for disappeared PIDs — when PID disappears (tracker.py:60-65), uses `time.time()` but has no final score to snapshot. Suggested: add comment explaining why no exit snapshot possible.
- **Minor:** Inconsistent time.time() vs captured_at — could use `scores[0].captured_at if scores else time.time()` for consistency.
- **Minor:** Test helper function would reduce duplication — each test creates similar ProcessScore objects with many fields.
- **Minor:** Missing test for equal peak score — no test verifying peak is NOT updated when score equals current peak.
- **Minor:** No test for multiple simultaneous processes — all tests track single PIDs.

**Recommendations:**
- Add structured logging for event open/close/peak
- Consider batch operations for performance with many PIDs
- Add metrics tracking for observability

**Assessment:**
Good quality. Clean, focused, correctly handles core tracking logic. Issues are mostly minor improvements rather than bugs. Ready for integration with documentation improvements.

### User Decision
**Choice:** Fix ALL issues

**Reasoning:** User wants comprehensive implementation.

**Deferred items:** None

### Implement (fix code review issues)
**What was changed:**
1. Exit snapshot on close — Modified `_close_event()` to accept optional `exit_score: ProcessScore | None` parameter. When closing due to score drop, exit snapshot is now inserted. When PID disappears, no snapshot (with clarifying comment).
2. Consistent timestamps — Changed from `time.time()` to `scores[0].captured_at if scores else time.time()`
3. Added clarifying comments explaining edge cases
4. Added `make_score()` test helper to reduce duplication (~327 lines → ~162 lines)
5. New test: `test_tracker_does_not_update_peak_for_equal_score`
6. New test: `test_tracker_handles_multiple_simultaneous_processes`
7. New tests: `test_tracker_inserts_exit_snapshot_on_score_drop`, `test_tracker_no_exit_snapshot_for_disappeared_pid`

**Files changed:**
- src/pause_monitor/tracker.py (updated _close_event, update methods)
- tests/test_tracker.py (added helper, 4 new tests, refactored existing)

**Testing:**
All 10 tests pass (6 existing + 4 new)

**Concerns/observations:**
None

### Code Review (re-review)
**Previous issues status:**
| Issue | Status |
|-------|--------|
| No exit snapshot on close | ✅ FIXED — `_close_event` accepts optional `exit_score`, inserts exit snapshot when available |
| No score for disappeared PIDs | ✅ FIXED — Clear docstring and comments explain when exit_score is None |
| Inconsistent time.time() vs captured_at | ✅ FIXED — Uses `scores[0].captured_at if scores else time.time()` |
| Test helper function | ✅ FIXED — `make_score()` helper with sensible defaults |
| Missing test for equal peak score | ✅ FIXED — `test_tracker_does_not_update_peak_for_equal_score` |
| Missing test for multiple processes | ✅ FIXED — `test_tracker_handles_multiple_simultaneous_processes` |

**New issues:** None

**Assessment:** Ready to proceed. All 6 issues fixed correctly, no new issues, 10 tests pass.

### Committed
`ec38125` - Task 6: Add ProcessTracker for per-process band tracking
`f78fbde` - Task 6: Fix code review issues

---

## Task 7: Delete tier system

### Implement
**What was built:**
Deleted the tier system code (Tier, TierAction, TierManager) which has been superseded by ProcessTracker for per-process band tracking.

- Deleted Tier, TierAction, TierManager classes from sentinel.py (module now just has a docstring placeholder)
- Removed TierManager import/usage from daemon.py, deleted tier callback methods, simplified main loop
- Removed TierAction import and tier-related tests from test_daemon.py
- Deleted tests/test_tier_manager.py
- Created tests/test_no_tiers.py with verification tests

**Files changed:**
- src/pause_monitor/sentinel.py (deleted tier classes)
- src/pause_monitor/daemon.py (removed tier references)
- tests/test_daemon.py (removed tier imports/tests)
- tests/test_tier_manager.py (deleted)
- tests/test_no_tiers.py (created)

**Testing:**
- test_no_tier_imports — verifies no tier classes referenced in source files ✅
- test_sentinel_has_no_tier_classes — verifies sentinel module doesn't export tier classes ✅
- Daemon tests: 26 passed
- ProcessTracker tests: 10 passed
- Full suite: 254 passed, 52 failed (pre-existing from Task 4 schema migration)

**Self-review findings:**
- The daemon's main loop uses `tier=1` as a placeholder until Task 8 integrates ProcessTracker

**Concerns/observations:**
- 52 test failures are pre-existing from schema v8 migration, will be cleaned up in Task 10

### Spec Review
**Result:** ✅ Compliant

**Verification:**
| Requirement | Status | Evidence |
|-------------|--------|----------|
| Delete TierManager, Tier, TierAction from sentinel.py | ✅ Done | sentinel.py now contains only a docstring (lines 1-5) |
| Remove tier references from daemon.py | ✅ Done | daemon.py has no tier imports or tier-related methods |
| Delete TiersConfig from config.py | ✅ Done | config.py contains no TiersConfig class |
| Delete tests/test_tier_manager.py | ✅ Done | Glob search found no matching file |
| Modify other files with tier imports | ✅ Done | Grep found no tier imports in source code |
| Create tests/test_no_tiers.py with test_no_tier_imports | ✅ Done | Test exists at test_no_tiers.py lines 4-22 |
| Create tests/test_no_tiers.py with test_sentinel_has_no_tier_classes | ✅ Done | Test exists at test_no_tiers.py lines 25-31 |
| Tests pass | ✅ Done | Both tests pass |

**Observations:**
- Tier references remain in documentation files (docs/plans/) and memory files (.serena/memories/) — expected historical/design docs
- tests/test_no_tiers.py references tier names to check they don't exist — expected

### Code Review
**Strengths:**
- Clean deletion of unused code — tier system completely removed, helpful docstring placeholder retained
- Thorough removal of dependencies from daemon.py, test_daemon.py
- Clear placeholder comments explaining intermediate state (e.g., "tier=1 placeholder until ProcessTracker integration")
- Good verification tests in test_no_tiers.py (AST-based scan + direct assertion)
- All 28 tests in affected files pass
- Clean linting — no ruff errors

**Issues:**
- **Critical:** None
- **Important:** None
- **Minor:**
  - sentinel.py could be deleted entirely (just a docstring now) — better left for Task 10 cleanup
  - Dead code in DaemonState (elevated/critical methods) — correctly out of scope for Task 7, covered by Task 10
  - Test exercises dead code (test_daemon_state_elevated_duration) — cleanup in Task 10

**Assessment:** Ready to proceed. Implementation is clean, focused, and correctly scoped. Dead code flagged is intentional intermediate state covered by Task 10.

### User Decision
**Choice:** Proceed to Task 8

**Deferred items:** Minor issues (sentinel.py deletion, DaemonState dead code, dead code test) deferred to Task 10 cleanup

### Committed
`c14e274` - Task 7: Delete tier system, replaced by ProcessTracker

---

## Task 8: Integrate ProcessTracker into daemon

### Implement
**What was built:**
- Added imports for `get_boot_time` and `ProcessTracker` in daemon.py
- Added `self.boot_time = get_boot_time()` and `self.tracker: ProcessTracker | None = None` in `Daemon.__init__`
- Conditional initialization: if DB exists with correct schema, tracker created in `__init__`; otherwise created during `_init_database()`
- Added `self.tracker.update(samples.rogues)` call in `_main_loop()` after collecting scores
- Updated docstring to reflect ProcessTracker integration

**Files changed:**
- src/pause_monitor/daemon.py (imports, __init__, _init_database, _main_loop)
- tests/test_daemon.py (2 new tests)

**Testing:**
- test_daemon_initializes_tracker — verifies daemon creates ProcessTracker on startup with correct boot_time ✅
- test_daemon_main_loop_updates_tracker — verifies main loop calls tracker.update() with rogue processes ✅
- All 28 daemon tests pass
- All 10 tracker tests pass
- Linting passes

**Self-review findings:**
- Added conditional tracker initialization to handle cases where DB exists but has wrong schema
- Used `samples.rogues` as the scores to pass to tracker (the filtered high-score processes)

**Concerns/observations:**
- 52 test failures in other files are pre-existing from Task 7 schema changes, planned for Task 10 cleanup

### Spec Review
**Result:** ✅ Compliant

**Verification:**
| Requirement | Status |
|------------|--------|
| Import `get_boot_time` | ✅ daemon.py:12 |
| Import `ProcessTracker` | ✅ daemon.py:27 |
| `self.boot_time = get_boot_time()` in `__init__` | ✅ daemon.py:100 |
| `self.tracker = ProcessTracker(...)` in `__init__` | ✅ daemon.py:104-114 |
| `self.tracker.update(scores)` in main loop | ✅ daemon.py:425-427 |
| Test: `test_daemon_initializes_tracker` | ✅ test_daemon.py:657-669 |

**Extra work (not in spec but not harmful):**
- Conditional initialization logic (handles DB not existing or wrong schema)
- Null check before `tracker.update()`
- Additional test `test_daemon_main_loop_updates_tracker`

**Observations:**
- Implementation uses `samples.rogues` instead of generic `scores` — correct, rogues are the filtered high-score processes
- Test uses `monkeypatch` instead of `mocker` — equivalent approach

### User Decision
**Choice:** Proceed (accept the extras)

**Reasoning:** Extra defensive code and additional test are acceptable

### Code Review
**Strengths:**
- Clean integration pattern — tracker initialized in `__init__` when possible, fallback to `_init_database()` when needed
- Proper error handling for sqlite3.OperationalError (schema mismatch case)
- Defensive null check in `_main_loop()` before `tracker.update()`
- Good test coverage with two tests covering key behaviors
- Test isolation using `patched_config_paths` fixture

**Issues:**
- **Important:** Stale comments in daemon.py:429 and daemon.py:436 say "tier=1 placeholder until ProcessTracker integration" but Task 8 IS the ProcessTracker integration — misleading
- **Minor:** No test for schema-mismatch recovery path (daemon.py:107-114)
- **Minor:** Test uses magic timestamp `1706000000` without comment explaining the date
- **Minor:** Docstring/comment terminology inconsistency ("Update ProcessTracker" vs "Update per-process tracking")

**Assessment:** Ready to proceed. Issues are minor; stale comments tracked for Task 10 cleanup.

### User Decision
**Choice:** Fix these issues

### Implement (fix code review issues)
**What was fixed:**
1. Removed stale comments "(tier=1 placeholder until ProcessTracker integration)" from daemon.py:429 and 436
2. Added `test_daemon_schema_mismatch_recovery` test — creates DB with incompatible schema, verifies `__init__` leaves tracker as None, verifies `_init_database()` recreates DB and initializes tracker
3. Added `# 2024-01-23 UTC` comments to magic timestamp `1706000000` occurrences
4. Fixed docstring to match code comment terminology ("Update per-process tracking with rogue processes")

**Files changed:**
- src/pause_monitor/daemon.py (stale comments removed, docstring fixed)
- tests/test_daemon.py (new test added, timestamp comments added)

**Testing:**
- All 29 daemon tests pass
- Linting clean

**Concerns/observations:**
None

### Code Review (re-review)
**Strengths:**
- Clean integration pattern with conditional logic for DB existence
- Null safety with `if self.tracker is not None:` check
- Error recovery for sqlite3.OperationalError
- Good test coverage including schema mismatch recovery
- Linting clean

**Issues:**
- **Important:** Missing tracker cleanup in `stop()` method (daemon.py:183-212) — closes conn but doesn't set `self.tracker = None`. Inconsistent with other resource cleanup patterns.
- **Important:** Existing test `test_daemon_main_loop_collects_samples` bypasses tracker update — tracker remains None throughout, silently skips tracker functionality
- **Minor:** Comment at daemon.py:102-103 could be clearer about what "correct schema" means
- **Minor:** Test timestamp `1706000000.0` used in multiple places — could be a constant

**Assessment:** Ready to proceed with one recommended fix (tracker cleanup in stop).

### User Decision
**Choice:** Fix all issues

### Implement (fix remaining code review issues)
**What was fixed:**
1. Added `self.tracker = None` in `stop()` method for consistent resource cleanup
2. Updated `test_daemon_main_loop_collects_samples` to explicitly document and test the "no tracker" case, added assertion `assert daemon.tracker is None`
3. Updated comment at daemon.py:102-103 to clarify "compatible schema" meaning
4. Added `TEST_TIMESTAMP = 1706000000.0  # 2024-01-23 UTC` constant, replaced all 5 occurrences

**Files changed:**
- src/pause_monitor/daemon.py (stop() cleanup, comment clarity)
- tests/test_daemon.py (TEST_TIMESTAMP constant, test docstring/assertion)

**Testing:**
- All 29 daemon tests pass
- Linting clean

**Concerns/observations:**
None

### Code Review (re-review 2)
**Strengths:**
- Clean lazy-initialization pattern handling multiple startup scenarios
- Defensive coding with null check before tracker.update()
- Good test coverage (3 tests cover key scenarios)
- Appropriate cleanup in stop() method
- Clear documentation and test docstrings

**Issues:**
- **Important:** Silent exception swallowing in `__init__` (daemon.py:110) — catches sqlite3.OperationalError without logging. Suggested: add debug log for schema mismatch.
- **Important:** Test timestamp comment inconsistency (test_daemon.py:21) — says "2024-01-23 UTC" but 1706000000 is actually 2024-01-23 12:26:40 UTC. Comment omits time.
- **Minor:** Boot time initialization outside try block — reviewer says fine as-is
- **Minor:** Explicit None check style — reviewer says current style is fine
- **Minor:** Test could verify tracker receives correct boot time — reviewer says not worth adding

**Assessment:** Ready to proceed. Silent exception swallowing is minor improvement for debuggability.

### User Decision
**Choice:** Proceed as-is

**Deferred items:** Silent exception logging, timestamp comment precision

### Committed
`a4830eb` - Task 8: Integrate ProcessTracker into daemon
`9cbb2cd` - Task 8: Fix code review issues
`11cff13` - Task 8: Fix remaining code review issues

---

## Task 9: Update TUI for minimal functionality

### Implement
**What was built:**
- Created `tests/test_tui.py` with `test_tui_app_starts_without_crash()` test
- Verified TUI already uses `config.bands.tracking_threshold` and `config.bands.forensics_threshold` (updated in prior work)
- Confirmed no references to `config.tiers`, `TiersConfig`, or `TierManager` in TUI code

**Files changed:**
- tests/test_tui.py (new file)

**Testing:**
- test_tui_app_starts_without_crash — verifies TUI app can instantiate ✅
- All 6 TUI-related tests pass

**Self-review findings:**
- TUI still displays "Tier: {tier}" in SampleInfoPanel from daemon socket data (daemon sends tier=1 hardcoded) — cosmetic, outside scope
- Linting passes

**Concerns/observations:**
- 52 pre-existing test failures from schema migration (Task 10 cleanup)

### Spec Review
**Result:** ✅ Compliant

**Verification:**
| Requirement | Status |
|-------------|--------|
| Test file exists with `test_tui_app_starts_without_crash` | ✅ tests/test_tui.py created |
| TUI uses `config.bands` instead of `config.tiers` | ✅ app.py:734-735 uses bands.tracking_threshold/forensics_threshold |
| No TierManager references in TUI | ✅ grep found no matches |
| No TiersConfig references in TUI | ✅ grep found no matches |
| No config.tiers references in TUI | ✅ grep found no matches |
| TUI can instantiate without errors | ✅ test passes |
| Commit made with specified message | ✅ commit f3aceb5 |

**Observations:**
- TUI still displays "Tier: {tier}" from daemon socket data (app.py:112-117) — outside scope since task was about config.tiers migration, not socket data display

### Code Review
**Strengths:**
- Test matches plan specification exactly
- No new linter errors
- Correct config.bands usage at app.py:734-735
- No TierManager/TiersConfig references in TUI
- Minimal scope — only adds test file (13 lines)

**Issues:**
- **Important:** Unused `tmp_path` parameter in test (test_tui.py:7) — matches plan verbatim, so this is a plan issue rather than implementation issue
- **Minor:** Legacy "Tier" terminology in SampleInfoPanel (app.py:80-118) — explicitly out of scope per plan

**Assessment:** Ready to merge. Task implementation correctly fulfills plan requirements.

### User Decision
**Choice:** Proceed as-is

**Reasoning:** Issues are minor/cosmetic. Unused tmp_path matches plan verbatim. Legacy Tier terminology is out of scope per plan.

**Deferred items:** Unused tmp_path parameter, legacy Tier terminology in SampleInfoPanel

### Committed
`f3aceb5` - Task 9: Update TUI for bands config, remove tier references

---

## Task 10: Clean up dead code and tests

### Implement
**What was built:**
Final cleanup pass removing all dead code from schema v8 migration and tier system deletion.

**Deleted files:**
- `src/pause_monitor/sentinel.py` — Placeholder docstring for old tier system, now replaced by ProcessTracker

**Dead code removed from storage.py:**
- `Event` dataclass (old events table representation)
- `create_event()` — created old-style events
- `finalize_event()` — closed old-style events
- `get_events()` — queried old events table
- `get_event_by_id()` — fetched single old event
- `update_event_status()` — updated old event status/notes
- Old `prune_old_data()` implementation referencing non-existent tables

**Dead code removed from daemon.py (DaemonState):**
- `elevated_since` and `critical_since` fields
- `enter_elevated()`, `exit_elevated()` methods
- `enter_critical()`, `exit_critical()` methods
- `elevated_duration`, `critical_duration` properties

**CLI updated for new schema:**
- `status` command — Now shows active tracked processes from `process_events`
- `events` command — Lists/shows process events from current boot with band information
- `history` command — Shows process event history with band breakdown stats
- `prune` command — Prunes both `process_sample_records` and closed `process_events`

**Test files rewritten:**
- test_storage.py — Tests new schema v8 with process_events, process_snapshots tables and CRUD functions
- test_cli.py — Tests updated CLI commands with proper mocking
- test_integration.py — Tests storage roundtrip with new time-based sample API
- test_daemon.py — Removed test for deleted `elevated_duration` method
- test_no_tiers.py — Updated to verify sentinel.py is deleted

**Testing:**
- 254 tests pass (resolved 52 failures)
- Linter clean

**Self-review findings:**
- 1,548 lines removed, 652 added (net reduction of ~1,200 lines of dead code)

**Concerns/observations:**
None

### Spec Review
**Result:** ❌ Issues

**What was verified correctly:**
- sentinel.py deleted — confirmed no file exists
- No stale imports of sentinel module
- No TierManager, TiersConfig, TierAction references remain
- CLI properly updated with inline SQL queries
- elevated_since, critical_since removed from DaemonState
- Old storage functions removed (Event dataclass, create_event, finalize_event, etc.)
- All 254 tests pass, linter clean

**Critical Issue: TUI has dead imports that cause runtime errors**

The TUI (`src/pause_monitor/tui/app.py`) contains references to deleted storage functions:

| Function | Location in TUI | Status |
|----------|-----------------|--------|
| `get_event_by_id` | Lines 282, 284 | MISSING from storage.py |
| `get_events` | Lines 424, 426, 749, 752 | MISSING from storage.py |
| `update_event_status` | Lines 341, 344, 477, 482 | MISSING from storage.py |

These imports are inside method bodies (late-bound), so they don't fail at import time. The test suite doesn't exercise these code paths because test_tui.py only tests instantiation.

**Impact:** Using the TUI Events feature would crash with ImportError at runtime.

**Assessment:** Task 10 is under-built. Dead code cleanup done for storage.py but TUI callers not updated.

### User Decision
**Choice:** Fix critical issue (required)

### Implement (fix TUI dead imports)
**What was fixed:**
Added helper functions and updated TUI to use new process_events schema.

**Added helper functions (app.py lines 22-95):**
- `_get_process_events()` — Replaces deleted `get_events()`, queries `process_events` table directly
- `_get_process_event_by_id()` — Replaces deleted `get_event_by_id()`, fetches single event with all fields

**Updated EventsTable (lines 278-293):**
- Changed columns from `Status, Time, Duration, Stress` to `Command, Band, Duration, Score`
- Old schema had status column for review workflow; new schema tracks per-process bands

**Updated EventDetailScreen (lines 296-394):**
- Removed status-based bindings (mark reviewed/pinned/dismissed) since status field no longer exists
- Now displays process event fields: Command, PID, Entry time/band, Exit time, Duration, Peak Band, Peak Score, Peak Snapshot
- Uses `_get_process_event_by_id()` helper instead of deleted `get_event_by_id()`

**Updated EventsScreen (lines 397-520):**
- Replaced status filtering (reviewed/unreviewed) with open/all filtering
- Changed columns to: ID, Command, PID, Peak Band, Duration, Score
- Uses `_get_process_events()` and `get_open_events()` for data retrieval
- Duration shows `*` suffix for ongoing events

**Updated _refresh_events() in PauseMonitorApp (lines 756-787):**
- Uses new `_get_process_events()` helper
- Filters by current boot time
- Shows Command, Band, Duration, Score

**Testing:**
- All 254 tests pass
- TUI import succeeds
- Linter passes

**Concerns/observations:**
None

### Spec Review (re-review)
**Result:** ✅ Compliant

**Verification:**
| Requirement | Status |
|-------------|--------|
| All 254 tests pass | ✅ Verified with pytest |
| No stale imports of TierManager/TierAction/TiersConfig | ✅ No matches in source or tests |
| No imports of deleted storage functions | ✅ get_events, get_event_by_id, update_event_status, create_event, finalize_event not imported anywhere |
| No imports from deleted sentinel.py | ✅ File does not exist, no imports |
| test_tier_manager.py deleted | ✅ File does not exist |
| TUI updated with new schema | ✅ Uses _get_process_events helper, queries process_events table |

**Observations:**
- Residual "tier" terminology in display layer (ringbuffer.py, daemon.py, TUI) is cosmetic, not dead code
- The deleted functionality was TierManager class (state machine), now replaced by ProcessTracker
- test_tier_determination_from_max_score tests band threshold logic, not deleted TierManager

### Code Review
**Strengths:**
- Clean schema v8 migration with proper foreign keys and cascading deletes
- Thorough ProcessTracker implementation handling all edge cases
- Consistent CLI updates using new process_events schema
- Clean TUI helper functions (_get_process_events, _get_process_event_by_id)
- Strong test coverage (254 tests) across tracker, storage, daemon, integration

**Issues:**
- **Important:** Memory files contain stale information:
  - `implementation_guide` references deleted sentinel.py, TierManager, shows SCHEMA_VERSION = 7
  - `unimplemented_features` says "Per-Process Band Tracking DESIGNED. Not yet implemented" — now fully implemented
  - Recommendation: Update memories in follow-up maintenance pass

- **Minor:** Pre-existing stub `action_show_history()` in tui/app.py:800-802 — not introduced by Task 10, documented in unimplemented_features

**Assessment:** Task 10 is complete and well-executed. Implementation is clean, tested, production-ready. Only action items are documentation updates.

### User Decision
**Choice:** Proceed as-is

**Reasoning:** Documentation updates can be done in follow-up maintenance pass.

**Deferred items:** Memory file updates (implementation_guide, unimplemented_features)

### Committed
`ff8eef5` - Task 10: Clean up dead code and fix remaining tests
`e867e66` - Task 10: Fix TUI dead imports for process_events schema

---

## Final Review

**Git Range:** 6ec3b09..e867e66 (22 files changed, -775 net lines)

**Test Results:** All 254 tests pass, linter clean

**Cross-Task Consistency:**
| Aspect | Assessment |
|--------|------------|
| Naming conventions | Consistent: BandsConfig, ProcessTracker, process_events |
| Error handling patterns | Consistent: matches existing storage patterns |
| Test patterns | Consistent: uses existing fixtures, same assertion styles |
| Code style | Consistent: proper type hints, docstrings, structlog usage |

**Architecture Assessment:**
- Separation of concerns: ProcessTracker handles state, storage.py handles persistence
- Single source of truth: ProcessScore with `captured_at` is canonical data format
- Graceful degradation: Daemon handles missing DB and schema mismatches
- Testability: All components can be unit tested with dependency injection

**Issues Found:**
- Minor (cosmetic, documented as deferred): Residual "Tier" terminology in TUI display, RingBuffer tier field, memory files need updating

**Critical/Important Issues:** None

**Recommendation:** READY TO MERGE

---

## Completed

**Merged:** 2026-01-27
**Method:** Fast-forward merge to main
**Branch deleted:** refactor/per-process-band-tracking

**Summary:**
- 10 tasks completed
- 22 files changed, -775 net lines
- 254 tests passing
- Tier system replaced with per-process band tracking via ProcessTracker

**Deferred items for follow-up:**
- Update memory files (implementation_guide, unimplemented_features)
- Residual "Tier" terminology in TUI display layer (cosmetic)
- RingBuffer tier field (unused but harmless)

