# Part 7: Integration

> **Navigation:** [Index](./index.md) | [Prev: Interface](./06-interface.md) | **Current** | End
>
> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Phases covered:** 13 (Final Integration)
**Tasks:** 33-36
**Dependencies:** All previous parts

---

## Phase 13: Final Integration

### Task 33: PID File Management

**Files:**
- Modify: `src/pause_monitor/daemon.py`

**Step 1: Add PID file handling**

Add to `Daemon` class in `src/pause_monitor/daemon.py`:

```python
    def _write_pid_file(self) -> None:
        """Write PID file."""
        self.config.pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.pid_path.write_text(str(os.getpid()))
        log.debug("pid_file_written", path=str(self.config.pid_path))

    def _remove_pid_file(self) -> None:
        """Remove PID file."""
        if self.config.pid_path.exists():
            self.config.pid_path.unlink()
            log.debug("pid_file_removed")

    def _check_already_running(self) -> bool:
        """Check if daemon is already running."""
        if not self.config.pid_path.exists():
            return False

        try:
            pid = int(self.config.pid_path.read_text().strip())
            # Check if process exists
            os.kill(pid, 0)
            return True
        except (ValueError, ProcessLookupError, PermissionError):
            # PID file exists but process doesn't - stale file
            self._remove_pid_file()
            return False
```

**Step 2: Wire PID file into start/stop**

Add to `start()` method after signal handlers:

```python
        # Check for existing instance
        if self._check_already_running():
            log.error("daemon_already_running")
            raise RuntimeError("Daemon is already running")

        self._write_pid_file()
```

Add to `stop()` method:

```python
        self._remove_pid_file()
```

**Step 3: Add os import at top of daemon.py**

```python
import os
```

**Step 4: Commit**

```bash
git add src/pause_monitor/daemon.py
git commit -m "feat(daemon): add PID file management"
```

---

### Task 34: Auto-Pruning Integration

**Files:**
- Modify: `src/pause_monitor/daemon.py`

**Step 1: Add auto-prune to daemon**

Add method to `Daemon` class:

```python
    async def _auto_prune(self) -> None:
        """Run automatic data pruning daily."""
        from pause_monitor.storage import prune_old_data

        while not self._shutdown_event.is_set():
            try:
                # Wait for 24 hours or shutdown
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=86400,  # 24 hours
                )
                break
            except asyncio.TimeoutError:
                # Run prune
                if self._conn:
                    prune_old_data(
                        self._conn,
                        samples_days=self.config.retention.samples_days,
                        events_days=self.config.retention.events_days,
                    )
```

**Step 2: Start auto-prune task in start()**

Add after `self.state.running = True`:

```python
        # Start auto-prune task
        asyncio.create_task(self._auto_prune())
```

**Step 3: Commit**

```bash
git add src/pause_monitor/daemon.py
git commit -m "feat(daemon): add automatic data pruning"
```

---

### Task 35: Full Test Suite Run

**Files:**
- None (verification only)

**Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass

**Step 2: Run linter**

Run: `uv run ruff check .`
Expected: No errors

**Step 3: Run formatter check**

Run: `uv run ruff format --check .`
Expected: All files formatted

**Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: address test and lint issues"
```

---

### Task 36: Documentation Update

**Files:**
- Verify: `CLAUDE.md`

**Step 1: Verify CLAUDE.md is accurate**

Review that the CLAUDE.md in the project root accurately describes all implemented commands and architecture.

**Step 2: Final commit**

```bash
git add -A
git commit -m "docs: finalize documentation"
```

---

## Summary

This implementation plan covers 36 tasks across 13 phases:

| Phase | Tasks | Description |
|-------|-------|-------------|
| 1 | 1-3 | Core Infrastructure (dependencies, config) |
| 2 | 4-7 | Stress Detection (StressBreakdown FIRST) |
| 3 | 8 | Test Infrastructure (conftest.py) |
| 4 | 9 | Storage Layer (schema) |
| 5 | 10-15 | Metrics Collection (Sample/Event, collector, policy) |
| 6 | 16-17 | Sleep/Wake and Pause Detection |
| 7 | 18-19 | Forensics Capture (spindump, tailspin, logs) |
| 8 | 20 | Notifications |
| 9 | 21-24 | Daemon Core (state, lifecycle, sampling, entry) |
| 10 | 25 | TUI Dashboard |
| 11 | 26-30 | CLI Commands (status, events, history, config, prune) |
| 12 | 31-32 | Install/Uninstall (modern launchctl) |
| 13 | 33-36 | Final Integration (PID file, auto-prune, tests, docs) |

**Key fixes from validation:**
- StressBreakdown defined in Phase 2 BEFORE storage operations in Phase 5
- Config uses `Config.load()` class method consistently
- Sample dataclass fields unified across all modules
- conftest.py provides shared test fixtures
- Streaming powermetrics subprocess (not exec-per-sample)
- tailspin integration for kernel traces
- Modern launchctl bootstrap/bootout syntax
- caffeinate for App Nap prevention
- Signal handlers for graceful shutdown
- Forensics and notifications wired to daemon
