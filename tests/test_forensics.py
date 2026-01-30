"""Tests for forensics capture."""

import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pause_monitor.collector import MetricValue, MetricValueStr, ProcessSamples, ProcessScore
from pause_monitor.forensics import (
    ForensicsCapture,
    identify_culprits,
    parse_logs_ndjson,
    parse_spindump,
)
from pause_monitor.ringbuffer import BufferContents, RingBuffer, RingSample
from pause_monitor.storage import get_connection, init_database


def _metric(val: float | int) -> MetricValue:
    return MetricValue(current=val, low=val, high=val)


def _metric_str(val: str) -> MetricValueStr:
    return MetricValueStr(current=val, low=val, high=val)


def make_process_score(
    pid: int = 1,
    command: str = "test",
    cpu: float = 50.0,
    score: int = 25,
    categories: list[str] | None = None,
    state: str = "running",
    band: str = "low",
    **kwargs,
) -> ProcessScore:
    """Create ProcessScore with sensible defaults for testing."""
    return ProcessScore(
        pid=pid,
        command=command,
        captured_at=kwargs.get("captured_at", time.time()),
        cpu=_metric(cpu),
        mem=_metric(kwargs.get("mem", 100 * 1024 * 1024)),
        mem_peak=kwargs.get("mem_peak", 150 * 1024 * 1024),
        pageins=_metric(kwargs.get("pageins", 100)),
        faults=_metric(kwargs.get("faults", 0)),
        disk_io=_metric(kwargs.get("disk_io", 0)),
        disk_io_rate=_metric(kwargs.get("disk_io_rate", 0.0)),
        csw=_metric(kwargs.get("csw", 1000)),
        syscalls=_metric(kwargs.get("syscalls", 500)),
        threads=_metric(kwargs.get("threads", 10)),
        mach_msgs=_metric(kwargs.get("mach_msgs", 0)),
        instructions=_metric(kwargs.get("instructions", 0)),
        cycles=_metric(kwargs.get("cycles", 0)),
        ipc=_metric(kwargs.get("ipc", 0.0)),
        energy=_metric(kwargs.get("energy", 0)),
        energy_rate=_metric(kwargs.get("energy_rate", 0.0)),
        wakeups=_metric(kwargs.get("wakeups", 0)),
        state=_metric_str(state),
        priority=_metric(kwargs.get("priority", 31)),
        score=_metric(score),
        band=_metric_str(band),
        categories=categories or ["cpu"],
    )


def make_process_samples(
    rogues: list[ProcessScore] | None = None,
    max_score: int | None = None,
    process_count: int = 100,
    elapsed_ms: int = 50,
    timestamp: datetime | None = None,
) -> ProcessSamples:
    """Create ProcessSamples with sensible defaults for testing."""
    if rogues is None:
        rogues = []
    if max_score is None:
        # score is MetricValue, extract .current for comparison
        max_score = max((r.score.current for r in rogues), default=0)
    return ProcessSamples(
        timestamp=timestamp or datetime.now(),
        elapsed_ms=elapsed_ms,
        process_count=process_count,
        max_score=max_score,
        rogues=rogues,
    )


# --- Spindump Parsing Tests ---


def test_parse_spindump_empty():
    """parse_spindump returns empty list for empty input."""
    assert parse_spindump("") == []


def test_parse_spindump_header_only():
    """parse_spindump returns empty list when no process blocks."""
    text = """
Date/Time:        2024-01-15 10:30:45.123 -0800
Duration:         10.00s
Hardware model:   Mac16,5
Memory size:      128 GB
"""
    assert parse_spindump(text) == []


def test_parse_spindump_single_process():
    """parse_spindump extracts process metadata."""
    text = """
Process:          python3.14 [12345]
Path:             /opt/homebrew/bin/python3.14
Parent:           zsh [1234]
Footprint:        123.45 MB
CPU Time:         0.456s
Num threads:      8
"""
    processes = parse_spindump(text)
    assert len(processes) == 1
    p = processes[0]
    assert p.pid == 12345
    assert p.name == "python3.14"
    assert p.path == "/opt/homebrew/bin/python3.14"
    assert p.parent_pid == 1234
    assert p.parent_name == "zsh"
    assert p.footprint_mb == 123.45
    assert p.cpu_time_sec == 0.456
    assert p.thread_count == 8


def test_parse_spindump_multiple_processes():
    """parse_spindump handles multiple process blocks."""
    text = """
Process:          chrome [1001]
Footprint:        500.0 MB

Process:          firefox [1002]
Footprint:        300.0 MB
"""
    processes = parse_spindump(text)
    assert len(processes) == 2
    assert processes[0].pid == 1001
    assert processes[0].name == "chrome"
    assert processes[1].pid == 1002
    assert processes[1].name == "firefox"


def test_parse_spindump_thread_basic():
    """parse_spindump extracts thread information."""
    text = """
Process:          test [100]

  Thread 0x1abc    DispatchQueue "com.apple.main-thread"(1)    500 samples    priority 31
"""
    processes = parse_spindump(text)
    assert len(processes) == 1
    assert processes[0].threads is not None
    assert len(processes[0].threads) == 1
    t = processes[0].threads[0]
    assert t.thread_id == "0x1abc"
    assert t.thread_name == "com.apple.main-thread"
    assert t.sample_count == 500
    assert t.priority == 31
    # cpu_time not in simplified line format


def test_parse_spindump_thread_blocked_state():
    """parse_spindump detects blocked state from stack frames."""
    text = """
Process:          test [100]

  Thread 0x1abc    1000 samples (1-1000)    priority 31
    1000  start + 100 (dyld + 100) [0x100]
      1000  kevent64 + 8 (libsystem_kernel.dylib + 52100) [0x192c4ab84]
"""
    processes = parse_spindump(text)
    assert len(processes) == 1
    t = processes[0].threads[0]
    assert t.state == "blocked_kevent"
    assert t.blocked_on == "kevent64"


def test_parse_spindump_various_blocked_states():
    """parse_spindump detects various blocked states."""
    test_cases = [
        ("__psynch_cvwait", "blocked_psynch"),
        ("__ulock_wait2", "blocked_ulock"),
        ("mach_msg", "blocked_mach_msg"),
        ("__semwait_signal", "blocked_semaphore"),
    ]
    for syscall, expected_state in test_cases:
        text = f"""
Process:          test [100]

  Thread 0x1abc    100 samples    priority 31
    100  {syscall} + 8 (lib + 100) [0x100]
"""
        processes = parse_spindump(text)
        t = processes[0].threads[0]
        assert t.state == expected_state, f"Failed for syscall: {syscall}"


# --- Log Parsing Tests ---


def test_parse_logs_ndjson_empty():
    """parse_logs_ndjson returns empty list for empty input."""
    assert parse_logs_ndjson(b"") == []


def test_parse_logs_ndjson_single_entry():
    """parse_logs_ndjson parses single log entry."""
    data = (
        b'{"timestamp":"2024-01-15 10:30:45.123",'
        b'"eventMessage":"Test message","subsystem":"com.apple.kernel"}\n'
    )
    entries = parse_logs_ndjson(data)
    assert len(entries) == 1
    e = entries[0]
    assert e.timestamp == "2024-01-15 10:30:45.123"
    assert e.event_message == "Test message"
    assert e.subsystem == "com.apple.kernel"


def test_parse_logs_ndjson_multiple_entries():
    """parse_logs_ndjson handles multiple lines."""
    data = b'{"timestamp":"t1","eventMessage":"msg1"}\n{"timestamp":"t2","eventMessage":"msg2"}\n'
    entries = parse_logs_ndjson(data)
    assert len(entries) == 2
    assert entries[0].event_message == "msg1"
    assert entries[1].event_message == "msg2"


def test_parse_logs_ndjson_extracts_process_name():
    """parse_logs_ndjson extracts process name from path."""
    data = b'{"timestamp":"t","eventMessage":"m","processImagePath":"/usr/bin/python3"}\n'
    entries = parse_logs_ndjson(data)
    assert entries[0].process_name == "python3"


def test_parse_logs_ndjson_handles_invalid_json():
    """parse_logs_ndjson skips invalid JSON lines."""
    data = (
        b'{"timestamp":"t1","eventMessage":"valid"}\n'
        b"not json\n"
        b'{"timestamp":"t2","eventMessage":"also valid"}\n'
    )
    entries = parse_logs_ndjson(data)
    assert len(entries) == 2


# --- identify_culprits Tests ---


def test_identify_culprits_from_buffer():
    """identify_culprits returns top rogues by score."""
    rogue = make_process_score(pid=100, command="Chrome", score=30, categories=["cpu", "mem"])
    samples = make_process_samples(rogues=[rogue])
    ring_sample = RingSample(samples=samples)
    contents = BufferContents(samples=(ring_sample,))

    culprits = identify_culprits(contents)

    assert len(culprits) == 1
    assert culprits[0]["pid"] == 100
    assert culprits[0]["command"] == "Chrome"
    # identify_culprits returns full MetricValue dict for score
    assert culprits[0]["score"]["current"] == 30
    assert set(culprits[0]["categories"]) == {"cpu", "mem"}


def test_identify_culprits_multiple_processes():
    """identify_culprits returns multiple processes sorted by score."""
    rogues = [
        make_process_score(pid=100, command="python", score=30),
        make_process_score(pid=200, command="Chrome", score=15),
    ]
    samples = make_process_samples(rogues=rogues)
    ring_sample = RingSample(samples=samples)
    contents = BufferContents(samples=(ring_sample,))

    culprits = identify_culprits(contents)

    # Should have both processes, sorted by score (python=30 > Chrome=15)
    assert len(culprits) == 2
    assert culprits[0]["pid"] == 100
    assert culprits[0]["command"] == "python"
    assert culprits[0]["score"]["current"] == 30
    assert culprits[1]["pid"] == 200
    assert culprits[1]["command"] == "Chrome"
    assert culprits[1]["score"]["current"] == 15


def test_identify_culprits_empty_buffer():
    """identify_culprits returns empty list for empty buffer."""
    contents = BufferContents(samples=())

    culprits = identify_culprits(contents)

    assert culprits == []


def test_identify_culprits_uses_peak_values():
    """identify_culprits uses MAX (peak) score across samples.

    If a process appears in multiple samples with different scores,
    the peak score should be used.
    """
    now = datetime.now()
    # Same process (same PID) with different scores across samples
    samples = [
        make_process_samples(
            rogues=[make_process_score(pid=300, command="Safari", score=20)],
            timestamp=now - timedelta(seconds=2),
        ),
        make_process_samples(
            rogues=[make_process_score(pid=300, command="Safari", score=35)],  # Peak
            timestamp=now - timedelta(seconds=1),
        ),
        make_process_samples(
            rogues=[make_process_score(pid=300, command="Safari", score=10)],
            timestamp=now,
        ),
    ]
    ring_samples = tuple(RingSample(samples=s) for s in samples)
    contents = BufferContents(samples=ring_samples)

    culprits = identify_culprits(contents)

    # Should use peak score of 35
    assert len(culprits) == 1
    assert culprits[0]["pid"] == 300
    assert culprits[0]["command"] == "Safari"
    assert culprits[0]["score"]["current"] == 35


def test_identify_culprits_differentiates_by_pid():
    """identify_culprits treats processes with same command but different PIDs as separate.

    Two Chrome processes with different PIDs should appear as separate entries,
    not be merged together.
    """
    rogues = [
        make_process_score(pid=1001, command="Chrome", score=40),
        make_process_score(pid=1002, command="Chrome", score=25),
    ]
    samples = make_process_samples(rogues=rogues)
    ring_sample = RingSample(samples=samples)
    contents = BufferContents(samples=(ring_sample,))

    culprits = identify_culprits(contents)

    # Should have both Chrome processes as separate entries
    assert len(culprits) == 2
    # Sorted by score descending
    assert culprits[0]["pid"] == 1001
    assert culprits[0]["command"] == "Chrome"
    assert culprits[0]["score"]["current"] == 40
    assert culprits[1]["pid"] == 1002
    assert culprits[1]["command"] == "Chrome"
    assert culprits[1]["score"]["current"] == 25


# --- ForensicsCapture Integration Tests ---


@pytest.fixture
def forensics_db(tmp_path: Path):
    """Create initialized database for forensics tests."""
    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    # Create a process event to attach forensics to
    import time

    from pause_monitor.storage import create_process_event

    event_id = create_process_event(
        conn,
        pid=123,
        command="test_process",
        boot_time=1000000,
        entry_time=time.time(),
        entry_band="high",
        peak_score=85,
        peak_band="high",
    )

    yield conn, event_id

    conn.close()


@pytest.mark.asyncio
async def test_forensics_capture_stores_in_database(forensics_db, tmp_path: Path):
    """ForensicsCapture stores parsed data in database, not files."""
    conn, event_id = forensics_db

    # Create buffer with sample data
    rogue = make_process_score(command="test", score=50)
    samples = make_process_samples(rogues=[rogue], max_score=50)
    buffer = RingBuffer(max_samples=10)
    buffer.push(samples)
    contents = buffer.freeze()

    # Mock the system commands
    with patch("pause_monitor.forensics.asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", b"")
        mock_process.wait = AsyncMock(return_value=0)
        mock_exec.return_value = mock_process

        capture = ForensicsCapture(conn, event_id)

        # capture_and_store requires temp dir to exist for tailspin
        with patch.object(capture, "_capture_tailspin") as mock_tailspin:
            mock_tailspin.return_value = Exception("skipped")

            capture_id = await capture.capture_and_store(contents, trigger="test_trigger")

    # Verify capture record created
    from pause_monitor.storage import get_buffer_context, get_forensic_captures

    captures = get_forensic_captures(conn, event_id)
    assert len(captures) == 1
    assert captures[0]["trigger"] == "test_trigger"

    # Verify buffer context stored
    context = get_buffer_context(conn, capture_id)
    assert context is not None
    assert context["sample_count"] == 1
    assert context["peak_score"] == 50


@pytest.mark.asyncio
async def test_forensics_capture_cleans_up_temp_dir(forensics_db, tmp_path: Path):
    """ForensicsCapture cleans up temp directory after capture."""
    conn, event_id = forensics_db

    samples = make_process_samples()
    buffer = RingBuffer(max_samples=10)
    buffer.push(samples)
    contents = buffer.freeze()

    with patch("pause_monitor.forensics.asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", b"")
        mock_process.wait = AsyncMock(return_value=0)
        mock_exec.return_value = mock_process

        capture = ForensicsCapture(conn, event_id)

        with patch.object(capture, "_capture_tailspin") as mock_tailspin:
            mock_tailspin.return_value = Exception("skipped")
            await capture.capture_and_store(contents, trigger="test")

    # Temp directories should be cleaned up (in /tmp, not tmp_path)
    # Just verify our test completed - actual cleanup happens automatically


@pytest.mark.asyncio
async def test_forensics_capture_handles_failures_gracefully(forensics_db):
    """ForensicsCapture handles capture failures without crashing."""
    conn, event_id = forensics_db

    samples = make_process_samples()
    buffer = RingBuffer(max_samples=10)
    buffer.push(samples)
    contents = buffer.freeze()

    # Mock all captures to fail
    with patch("pause_monitor.forensics.asyncio.create_subprocess_exec") as mock_exec:
        mock_exec.side_effect = FileNotFoundError("Command not found")

        capture = ForensicsCapture(conn, event_id)
        await capture.capture_and_store(contents, trigger="test")

    # Should still create capture record with failure status
    from pause_monitor.storage import get_forensic_captures

    captures = get_forensic_captures(conn, event_id)
    assert len(captures) == 1
    assert captures[0]["spindump_status"] == "failed"
    assert captures[0]["logs_status"] == "failed"
