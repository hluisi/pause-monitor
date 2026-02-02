"""Forensics capture for process events.

Captures forensic data (spindump, tailspin, logs) and stores parsed results
in the database. Raw captures go to /tmp, get parsed, results stored in DB,
temp files discarded.
"""

import asyncio
import json
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from rogue_hunter.storage import (
    create_forensic_capture,
    insert_buffer_context,
    insert_log_entry,
    insert_spindump_process,
    insert_spindump_thread,
    update_forensic_capture_status,
)

if TYPE_CHECKING:
    from rogue_hunter.ringbuffer import BufferContents

log = structlog.get_logger()


# --- Parsing Data Structures ---


@dataclass
class SpindumpThread:
    """Parsed thread from spindump output."""

    thread_id: str
    thread_name: str | None = None
    sample_count: int | None = None
    priority: int | None = None
    cpu_time_sec: float | None = None
    state: str | None = None
    blocked_on: str | None = None


@dataclass
class SpindumpProcess:
    """Parsed process from spindump output."""

    pid: int
    name: str
    path: str | None = None
    parent_pid: int | None = None
    parent_name: str | None = None
    footprint_mb: float | None = None
    cpu_time_sec: float | None = None
    thread_count: int | None = None
    threads: list[SpindumpThread] | None = None


@dataclass
class LogEntry:
    """Parsed log entry from ndjson output."""

    timestamp: str
    event_message: str
    mach_timestamp: int | None = None
    subsystem: str | None = None
    category: str | None = None
    process_name: str | None = None
    process_id: int | None = None
    message_type: str | None = None


# --- Parsing Functions ---


def parse_spindump(text: str) -> list[SpindumpProcess]:
    """Parse spindump text output into structured data.

    Spindump format:
    - Header section with Date/Time, Duration, etc.
    - Process blocks starting with "Process: name [pid]"
    - Thread blocks indented under processes

    Args:
        text: Raw spindump stdout text

    Returns:
        List of SpindumpProcess with nested threads
    """
    processes: list[SpindumpProcess] = []
    current_process: SpindumpProcess | None = None
    current_threads: list[SpindumpThread] = []

    # Regex patterns
    process_pattern = re.compile(r"^Process:\s+(.+?)\s+\[(\d+)\]")
    path_pattern = re.compile(r"^Path:\s+(.+)")
    parent_pattern = re.compile(r"^Parent:\s+(.+?)\s+\[(\d+)\]")
    footprint_pattern = re.compile(r"^Footprint:\s+([\d.]+)\s*MB")
    cpu_time_pattern = re.compile(r"^CPU Time:\s+([\d.]+)s")
    num_threads_pattern = re.compile(r"^Num threads:\s+(\d+)")

    # Thread pattern: "  Thread 0x516df    DispatchQueue "name"(1)    1001 samples..."
    thread_pattern = re.compile(
        r"^\s+Thread\s+(0x[0-9a-f]+)"
        r"(?:\s+DispatchQueue\s+\"([^\"]+)\")?.*?"
        r"(\d+)\s+samples?"
        r".*?priority\s+(\d+)"
        r"(?:.*?cpu time\s+([\d.]+)s)?",
        re.IGNORECASE,
    )

    # Blocked state patterns from stack frames
    blocked_patterns = {
        "kevent64": "blocked_kevent",
        "kevent": "blocked_kevent",
        "__psynch_cvwait": "blocked_psynch",
        "__psynch_mutexwait": "blocked_psynch",
        "__ulock_wait": "blocked_ulock",
        "__ulock_wait2": "blocked_ulock",
        "mach_msg": "blocked_mach_msg",
        "__semwait_signal": "blocked_semaphore",
        "__select": "blocked_select",
        "__workq_kernreturn": "blocked_workq",
        "(running)": "running",
    }

    lines = text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # Check for new process
        proc_match = process_pattern.match(line)
        if proc_match:
            # Save previous process
            if current_process is not None:
                current_process.threads = current_threads
                processes.append(current_process)

            current_process = SpindumpProcess(
                pid=int(proc_match.group(2)),
                name=proc_match.group(1),
            )
            current_threads = []
            i += 1
            continue

        if current_process is not None:
            # Parse process metadata
            if path_match := path_pattern.match(line):
                current_process.path = path_match.group(1)
            elif parent_match := parent_pattern.match(line):
                current_process.parent_name = parent_match.group(1)
                current_process.parent_pid = int(parent_match.group(2))
            elif footprint_match := footprint_pattern.match(line):
                current_process.footprint_mb = float(footprint_match.group(1))
            elif cpu_match := cpu_time_pattern.match(line):
                current_process.cpu_time_sec = float(cpu_match.group(1))
            elif threads_match := num_threads_pattern.match(line):
                current_process.thread_count = int(threads_match.group(1))

            # Parse thread header
            elif thread_match := thread_pattern.match(line):
                thread = SpindumpThread(
                    thread_id=thread_match.group(1),
                    thread_name=thread_match.group(2),
                    sample_count=int(thread_match.group(3)),
                    priority=int(thread_match.group(4)),
                )
                if thread_match.group(5):
                    thread.cpu_time_sec = float(thread_match.group(5))

                # Look ahead for blocked state in stack frames
                j = i + 1
                while j < len(lines) and lines[j].startswith("    "):
                    stack_line = lines[j]
                    for pattern, state in blocked_patterns.items():
                        if pattern in stack_line:
                            thread.state = state
                            thread.blocked_on = pattern
                            break
                    if thread.state:
                        break
                    j += 1

                current_threads.append(thread)

        i += 1

    # Don't forget the last process
    if current_process is not None:
        current_process.threads = current_threads
        processes.append(current_process)

    return processes


def parse_logs_ndjson(data: bytes) -> list[LogEntry]:
    """Parse ndjson log output into structured entries.

    The `log show --style ndjson` command outputs one JSON object per line.

    Args:
        data: Raw bytes from log show stdout

    Returns:
        List of LogEntry objects
    """
    entries: list[LogEntry] = []

    for line in data.decode("utf-8", errors="replace").split("\n"):
        line = line.strip()
        if not line:
            continue

        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Extract process name from path
        process_name = None
        if process_path := obj.get("processImagePath"):
            process_name = Path(process_path).name

        entry = LogEntry(
            timestamp=obj.get("timestamp", ""),
            event_message=obj.get("eventMessage", ""),
            mach_timestamp=obj.get("machTimestamp"),
            subsystem=obj.get("subsystem"),
            category=obj.get("category"),
            process_name=process_name,
            process_id=obj.get("processID"),
            message_type=obj.get("messageType"),
        )
        entries.append(entry)

    return entries


def identify_culprits(contents: "BufferContents") -> list[dict]:
    """Identify top culprit processes from ring buffer samples.

    With per-process scoring, rogues are already identified and scored.
    This function aggregates across all samples to find the peak offenders.

    Args:
        contents: Frozen ring buffer contents with samples

    Returns:
        List of ProcessScore-compatible dicts with MetricValue format for score.
        Each dict has: pid, command, score (as MetricValue dict), categories.
        Sorted by score descending.
        Processes are keyed by PID, so two processes with the same command
        but different PIDs are treated as separate entries.
    """
    if not contents.samples:
        return []

    # Track max score per process (keyed by PID)
    # Processes can appear in multiple samples; we want peak score
    peak_scores: dict[int, dict] = {}

    for sample in contents.samples:
        for rogue in sample.samples.rogues:
            existing = peak_scores.get(rogue.pid)
            if existing is None or rogue.score > existing["score"]:
                peak_scores[rogue.pid] = {
                    "pid": rogue.pid,
                    "command": rogue.command,
                    "score": rogue.score,
                    "dominant_resource": rogue.dominant_resource,
                    "disproportionality": rogue.disproportionality,
                }

    # Sort by score descending
    culprits = sorted(
        peak_scores.values(),
        key=lambda c: c["score"],
        reverse=True,
    )
    return culprits


# --- ForensicsCapture Class ---


class ForensicsCapture:
    """Captures forensic data and stores in database.

    Raw captures (spindump, tailspin, logs) are written to a temp directory,
    parsed for insights, and the parsed data is stored in the database.
    Temp files are cleaned up after processing.
    """

    def __init__(self, conn: sqlite3.Connection, event_id: int, runtime_dir: Path):
        """Initialize forensics capture.

        Args:
            conn: Database connection
            event_id: The process event ID this capture is associated with
            runtime_dir: Directory for tailspin captures (must match sudoers rule)
        """
        self.conn = conn
        self.event_id = event_id
        self._runtime_dir = runtime_dir
        self._temp_dir: Path | None = None

    async def capture_and_store(
        self,
        contents: "BufferContents",
        trigger: str,
    ) -> int:
        """Run full forensics capture: raw → parse → DB → cleanup.

        Captures tailspin (via sudo) and system logs. Live spindump is not
        captured because tailspin provides better data (kernel activity during
        the pause, not just process state after recovery).

        Args:
            contents: Frozen ring buffer contents
            trigger: What triggered this capture (e.g., 'band_entry_high')

        Returns:
            The capture_id of the created forensic_captures record
        """
        # Create temp directory (for logs capture)
        self._temp_dir = Path(tempfile.mkdtemp(prefix="rogue-hunter-"))
        log.debug("forensics_temp_dir", path=str(self._temp_dir))

        try:
            # Create capture record
            capture_id = create_forensic_capture(self.conn, self.event_id, trigger)

            # Run captures in parallel (no timeouts - let them complete)
            # Note: tailspin writes to runtime_dir, logs to _temp_dir
            tailspin_result, logs_result = await asyncio.gather(
                self._capture_tailspin(),
                self._capture_logs(),
                return_exceptions=True,
            )

            # Parse and store each capture type
            tailspin_status = self._process_tailspin(capture_id, tailspin_result)
            logs_status = self._process_logs(capture_id, logs_result)

            # Store buffer context
            self._store_buffer_context(capture_id, contents)

            # Update capture status (spindump no longer captured)
            update_forensic_capture_status(
                self.conn,
                capture_id,
                spindump_status=None,
                tailspin_status=tailspin_status,
                logs_status=logs_status,
            )

            log.info(
                "forensics_capture_complete",
                capture_id=capture_id,
                event_id=self.event_id,
                trigger=trigger,
                tailspin=tailspin_status,
                logs=logs_status,
            )

            return capture_id

        finally:
            # Always clean up temp directory
            if self._temp_dir and self._temp_dir.exists():
                shutil.rmtree(self._temp_dir)
                log.debug("forensics_temp_cleanup", path=str(self._temp_dir))

    async def _capture_tailspin(self) -> Path:
        """Capture tailspin to runtime directory. Requires sudo.

        Writes to runtime_dir (from config) which must match the path
        in the sudoers rule installed during `rogue-hunter install`.

        Returns:
            Path to the tailspin file

        Raises:
            PermissionError: If sudo -n fails (sudoers not configured)
            FileNotFoundError: If tailspin doesn't create output
        """
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        output_path = self._runtime_dir / f"capture_{self.event_id}.tailspin"

        process = await asyncio.create_subprocess_exec(
            "/usr/bin/sudo",
            "-n",  # Non-interactive, fail if password needed
            "/usr/bin/tailspin",
            "save",
            "-o",
            str(output_path),
            stdin=asyncio.subprocess.DEVNULL,  # No tty interaction
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,  # Capture stderr for error messages
            start_new_session=True,  # Detach from controlling terminal
        )
        _, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            raise PermissionError(f"tailspin save failed (sudo -n): {error_msg}")

        if not output_path.exists():
            raise FileNotFoundError(f"Tailspin did not create output: {output_path}")

        return output_path

    async def _capture_logs(self) -> bytes:
        """Capture logs as NDJSON, return raw bytes.

        Returns:
            Raw ndjson log output bytes

        Raises:
            Exception on failure
        """
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/log",
            "show",
            "--style",
            "ndjson",
            "--last",
            "60s",
            "--predicate",
            'subsystem == "com.apple.powerd" OR '
            'subsystem == "com.apple.kernel" OR '
            'subsystem == "com.apple.windowserver" OR '
            'eventMessage CONTAINS[c] "hang" OR '
            'eventMessage CONTAINS[c] "stall" OR '
            'eventMessage CONTAINS[c] "timeout"',
            stdin=asyncio.subprocess.DEVNULL,  # No tty interaction
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,  # Detach from controlling terminal
        )

        stdout, _ = await process.communicate()

        return stdout

    def _process_tailspin(
        self,
        capture_id: int,
        result: Path | BaseException,
    ) -> str:
        """Decode tailspin via spindump -i and store in DB.

        Tailspin files are binary. We decode them using:
            spindump -i <file> -stdout

        This produces the same text format as regular spindump.

        Args:
            capture_id: The forensic capture ID
            result: Path to tailspin file or exception

        Returns:
            Status string: 'success' or 'failed'
        """
        if isinstance(result, BaseException):
            log.warning("tailspin_failed", error=str(result))
            return "failed"

        try:
            # Decode tailspin using spindump synchronously (no timeout - let it complete)
            import subprocess

            completed = subprocess.run(
                ["/usr/sbin/spindump", "-i", str(result), "-stdout"],
                stdin=subprocess.DEVNULL,  # No tty interaction
                capture_output=True,
                start_new_session=True,  # Detach from controlling terminal
            )

            if completed.returncode != 0:
                log.warning("tailspin_decode_failed", returncode=completed.returncode)
                return "failed"

            text = completed.stdout.decode("utf-8", errors="replace")
            processes = parse_spindump(text)

            # Store with same logic as spindump (tailspin data goes into same tables)
            for proc in processes:
                proc_id = insert_spindump_process(
                    self.conn,
                    capture_id=capture_id,
                    pid=proc.pid,
                    name=proc.name,
                    path=proc.path,
                    parent_pid=proc.parent_pid,
                    parent_name=proc.parent_name,
                    footprint_mb=proc.footprint_mb,
                    cpu_time_sec=proc.cpu_time_sec,
                    thread_count=proc.thread_count,
                )

                if proc.threads:
                    for thread in proc.threads:
                        insert_spindump_thread(
                            self.conn,
                            process_id=proc_id,
                            thread_id=thread.thread_id,
                            thread_name=thread.thread_name,
                            sample_count=thread.sample_count,
                            priority=thread.priority,
                            cpu_time_sec=thread.cpu_time_sec,
                            state=thread.state,
                            blocked_on=thread.blocked_on,
                        )

            log.info("tailspin_parsed", process_count=len(processes))
            return "success"

        except Exception as e:
            log.warning("tailspin_decode_failed", error=str(e))
            return "failed"

    def _process_logs(
        self,
        capture_id: int,
        result: bytes | BaseException,
    ) -> str:
        """Parse NDJSON log lines and store in DB.

        Args:
            capture_id: The forensic capture ID
            result: Raw ndjson bytes or exception

        Returns:
            Status string: 'success' or 'failed'
        """
        if isinstance(result, BaseException):
            log.warning("logs_failed", error=str(result))
            return "failed"

        try:
            entries = parse_logs_ndjson(result)

            for entry in entries:
                insert_log_entry(
                    self.conn,
                    capture_id=capture_id,
                    timestamp=entry.timestamp,
                    event_message=entry.event_message,
                    mach_timestamp=entry.mach_timestamp,
                    subsystem=entry.subsystem,
                    category=entry.category,
                    process_name=entry.process_name,
                    process_id=entry.process_id,
                    message_type=entry.message_type,
                )

            log.info("logs_parsed", entry_count=len(entries))
            return "success"

        except Exception as e:
            log.warning("logs_parse_failed", error=str(e))
            return "failed"

    def _store_buffer_context(
        self,
        capture_id: int,
        contents: "BufferContents",
    ) -> None:
        """Store ring buffer context in database.

        Args:
            capture_id: The forensic capture ID
            contents: Frozen ring buffer contents
        """
        culprits = identify_culprits(contents)
        peak_score = max((c["score"] for c in culprits), default=0)

        insert_buffer_context(
            self.conn,
            capture_id=capture_id,
            sample_count=len(contents.samples),
            peak_score=peak_score,
            culprits=json.dumps(culprits),
        )
