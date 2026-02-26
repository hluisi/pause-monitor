# Forensics Capture Refactor Plan

## Problem Statement

The current forensics capture system has three issues:
1. **Blocking event loop**: `_process_tailspin()` uses `subprocess.run()` (blocking) and does sync DB inserts, freezing the daemon
2. **Ring buffer degradation**: We freeze the full buffer but only store a JSON summary of top culprits, losing 99% of the data
3. **Poor async hygiene**: Synchronous code in async methods

## Solution Overview

1. Make spindump decode async via `asyncio.create_subprocess_exec()`
2. Offload DB inserts to thread pool via `run_in_executor()`
3. Store FULL ring buffer contents (all processes from all samples)
4. Schema v20 → v21 for new buffer storage tables

## Design Decisions

- **Buffer scope**: Store ALL processes from each sample (6,000-12,000 rows per capture) for complete forensic context
- **Async pattern**: Full async (Option B) - async subprocess for decode, executor only for DB inserts
- **Threading**: Pass `db_path` to ForensicsCapture, create thread-local connections for executor work

---

## Implementation

### Phase 1: Schema Changes (`storage.py`)

**Bump SCHEMA_VERSION 20 → 21**

**Add new tables:**

```sql
-- Buffer samples (one per ring buffer sample)
CREATE TABLE IF NOT EXISTS buffer_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id INTEGER NOT NULL,
    sample_index INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    elapsed_ms INTEGER NOT NULL,
    process_count INTEGER NOT NULL,
    max_score REAL NOT NULL,
    FOREIGN KEY (context_id) REFERENCES buffer_context(id) ON DELETE CASCADE
);
CREATE INDEX idx_buffer_samples_context ON buffer_samples(context_id);

-- Buffer sample processes (full ProcessScore per process per sample)
CREATE TABLE IF NOT EXISTS buffer_sample_processes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    command TEXT NOT NULL,
    captured_at REAL NOT NULL,
    cpu REAL,
    mem INTEGER,
    mem_peak INTEGER,
    -- ... all 47 ProcessScore fields (copy from machine_snapshot_processes) ...
    FOREIGN KEY (sample_id) REFERENCES buffer_samples(id) ON DELETE CASCADE
);
CREATE INDEX idx_buffer_sample_processes_sample ON buffer_sample_processes(sample_id);
```

**Add functions:**
- `insert_buffer_sample(conn, context_id, sample_index, timestamp, elapsed_ms, process_count, max_score) -> int`
- `insert_buffer_sample_processes(conn, sample_id, processes: list[ProcessScore])` - use `executemany`

### Phase 2: Forensics Refactor (`forensics.py`)

**Change ForensicsCapture.__init__:**
```python
def __init__(
    self,
    db_path: Path,  # Changed from conn: sqlite3.Connection
    event_id: int,
    runtime_dir: Path,
    log_seconds: int = 60,
):
    self._db_path = db_path
    # ... rest unchanged
```

**Convert _process_tailspin to async with executor for DB:**
```python
async def _process_tailspin(self, capture_id: int, result: Path | BaseException) -> str:
    if isinstance(result, BaseException):
        return "failed"

    try:
        # Async subprocess for decode (doesn't block event loop)
        proc = await asyncio.create_subprocess_exec(
            "/usr/sbin/spindump", "-i", str(result), "-stdout",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            return "failed"

        # Parse (CPU work, but fast enough to stay on event loop)
        text = stdout.decode("utf-8", errors="replace")
        data = parse_tailspin(text)

        # DB inserts in executor (many writes, use thread)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._store_tailspin_data,
            capture_id,
            data,
        )
        return "success"
    except Exception:
        log.warning("tailspin_process_failed", exc_info=True)
        return "failed"

def _store_tailspin_data(self, capture_id: int, data: TailspinData) -> None:
    """Store parsed tailspin data. Runs in executor thread."""
    conn = sqlite3.connect(self._db_path)
    try:
        # All the insert_tailspin_* calls currently in _process_tailspin
        insert_tailspin_header(conn, capture_id, ...)
        for proc in data.processes:
            proc_id = insert_tailspin_process(conn, capture_id, ...)
            # ... threads, frames, etc.
        conn.commit()
    finally:
        conn.close()
```

**Same pattern for _process_logs** (already uses async subprocess, just move DB inserts to executor)

**Rewrite _store_buffer_context for full storage:**
```python
async def _store_buffer_context(self, capture_id: int, contents: BufferContents) -> None:
    """Store full ring buffer contents. Runs DB work in executor."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        self._store_buffer_data,
        capture_id,
        contents,
    )

def _store_buffer_data(self, capture_id: int, contents: BufferContents) -> None:
    """Store full buffer data. Runs in executor thread."""
    conn = sqlite3.connect(self._db_path)
    try:
        # Create buffer_context header
        context_id = insert_buffer_context(conn, capture_id, len(contents.samples), ...)

        # Store each sample with ALL processes
        for idx, ring_sample in enumerate(contents.samples):
            samples = ring_sample.samples  # ProcessSamples
            sample_id = insert_buffer_sample(
                conn, context_id, idx,
                samples.timestamp.isoformat(),
                samples.elapsed_ms,
                samples.process_count,
                samples.max_score,
            )

            # Store ALL processes from all_by_pid
            all_processes = list(samples.all_by_pid.values())
            insert_buffer_sample_processes(conn, sample_id, all_processes)

        conn.commit()
    finally:
        conn.close()
```

**Update capture_and_store to make all processing async:**
```python
async def capture_and_store(self, contents: BufferContents, trigger: str) -> int:
    # Create capture record (quick, keep sync)
    conn = sqlite3.connect(self._db_path)
    try:
        capture_id = create_forensic_capture(conn, self.event_id, trigger)
    finally:
        conn.close()

    # Parallel async captures
    tailspin_result, logs_result = await asyncio.gather(
        self._capture_tailspin(),
        self._capture_logs(),
        return_exceptions=True,
    )

    # Parallel async processing (each uses executor internally for DB)
    tailspin_status, logs_status, _ = await asyncio.gather(
        self._process_tailspin(capture_id, tailspin_result),
        self._process_logs(capture_id, logs_result),
        self._store_buffer_context(capture_id, contents),
        return_exceptions=True,
    )

    # Handle exceptions from gather
    if isinstance(tailspin_status, BaseException):
        tailspin_status = "failed"
    if isinstance(logs_status, BaseException):
        logs_status = "failed"

    # Update status (quick, use fresh connection)
    conn = sqlite3.connect(self._db_path)
    try:
        update_forensic_capture_status(conn, capture_id, ...)
    finally:
        conn.close()

    return capture_id
```

### Phase 3: Daemon Integration (`daemon.py`)

**Update _forensics_callback:**
```python
capture = ForensicsCapture(
    self.config.db_path,  # Changed from self._conn
    event_id,
    self.config.runtime_dir,
    log_seconds=self.config.system.forensics_log_seconds,
)
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `src/rogue_hunter/storage.py` | Schema v21, new tables, new insert functions |
| `src/rogue_hunter/forensics.py` | Async refactor, full buffer storage, thread-local connections |
| `src/rogue_hunter/daemon.py` | Pass db_path instead of conn |
| `tests/test_forensics.py` | Update tests for new API |
| `tests/test_storage.py` | Add tests for new tables, update schema version test |

---

## Verification

1. **Lint check**: `uv run ruff check . && uv run ruff format .`
2. **Run tests**: `uv run pytest`
3. **Manual test**:
   - Start daemon: `uv run rogue-hunter daemon`
   - Start TUI in another terminal: `uv run rogue-hunter tui`
   - Trigger high CPU process to cause forensic capture
   - Verify TUI remains responsive during capture
   - Check database has full buffer data: `sqlite3 ~/.local/share/rogue-hunter/data.db "SELECT COUNT(*) FROM buffer_sample_processes"`
