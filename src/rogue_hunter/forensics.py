"""Forensics capture for process events.

Captures forensic data (tailspin, logs) and stores parsed results
in the database. Raw captures go to /tmp, get parsed, results stored in DB.
"""

import asyncio
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from rogue_hunter.storage import (
    create_forensic_capture,
    insert_buffer_context,
    insert_log_entry,
    insert_tailspin_binary_image,
    insert_tailspin_frame,
    insert_tailspin_header,
    insert_tailspin_io_aggregate,
    insert_tailspin_io_histogram,
    insert_tailspin_io_stats,
    insert_tailspin_process,
    insert_tailspin_process_note,
    insert_tailspin_shared_cache,
    insert_tailspin_thread,
    update_forensic_capture_status,
)

if TYPE_CHECKING:
    from rogue_hunter.ringbuffer import BufferContents

log = structlog.get_logger()


# --- Parsing Data Structures ---


@dataclass
class TailspinFrame:
    """Parsed stack frame from spindump output."""

    sample_count: int
    is_kernel: bool
    address: str
    depth: int
    symbol_name: str | None = None
    symbol_offset: int | None = None
    library_name: str | None = None
    library_offset: int | None = None
    state: str | None = None
    core_type: str | None = None
    blocked_on: str | None = None
    children: list["TailspinFrame"] = field(default_factory=list)


@dataclass
class TailspinThread:
    """Parsed thread from spindump output."""

    thread_id: str
    dispatch_queue_name: str | None = None
    dispatch_queue_serial: int | None = None
    thread_name: str | None = None
    num_samples: int | None = None
    sample_range_start: int | None = None
    sample_range_end: int | None = None
    priority: int | None = None
    base_priority: int | None = None
    cpu_time_sec: float | None = None
    cycles: int | None = None
    instructions: int | None = None
    cpi: float | None = None
    io_count: int | None = None
    io_bytes: int | None = None
    frames: list[TailspinFrame] = field(default_factory=list)


@dataclass
class TailspinBinaryImage:
    """Parsed binary image from spindump output."""

    start_address: str
    end_address: str | None
    name: str
    version: str | None
    uuid: str | None
    path: str | None
    is_kernel: bool


@dataclass
class TailspinProcess:
    """Parsed process from spindump output."""

    pid: int
    name: str
    uuid: str | None = None
    path: str | None = None
    identifier: str | None = None
    version: str | None = None
    parent_pid: int | None = None
    parent_name: str | None = None
    responsible_pid: int | None = None
    responsible_name: str | None = None
    execed_from_pid: int | None = None
    execed_from_name: str | None = None
    execed_to_pid: int | None = None
    execed_to_name: str | None = None
    architecture: str | None = None
    shared_cache_uuid: str | None = None
    runningboard_managed: bool | None = None
    sudden_term: str | None = None
    footprint_mb: float | None = None
    footprint_delta_mb: float | None = None
    io_count: int | None = None
    io_bytes: int | None = None
    time_since_fork_sec: int | None = None
    start_time: str | None = None
    end_time: str | None = None
    num_samples: int | None = None
    sample_range_start: int | None = None
    sample_range_end: int | None = None
    cpu_time_sec: float | None = None
    cycles: int | None = None
    instructions: int | None = None
    cpi: float | None = None
    num_threads: int | None = None
    notes: list[str] = field(default_factory=list)
    threads: list[TailspinThread] = field(default_factory=list)
    binary_images: list[TailspinBinaryImage] = field(default_factory=list)


@dataclass
class TailspinSharedCache:
    """Parsed shared cache entry from header."""

    uuid: str
    base_address: str
    slide: str
    name: str


@dataclass
class TailspinIOStats:
    """Parsed I/O statistics."""

    tier: str
    io_count: int
    io_rate: float | None
    bytes_total: int
    bytes_rate: float | None


@dataclass
class TailspinIOHistogramBucket:
    """Parsed I/O histogram bucket."""

    histogram_type: str
    begin_value: int
    end_value: int | None
    frequency: int
    cdf: int


@dataclass
class TailspinIOAggregate:
    """Parsed I/O aggregate stats."""

    tier: str
    num_ios: int
    latency_mean_us: int | None = None
    latency_max_us: int | None = None
    latency_sd_us: int | None = None
    read_count: int | None = None
    read_bytes: int | None = None
    write_count: int | None = None
    write_bytes: int | None = None


@dataclass
class TailspinHeader:
    """Parsed header from spindump output."""

    start_time: str
    end_time: str
    duration_sec: float
    steps: int
    sampling_interval_ms: int
    os_version: str
    architecture: str
    report_version: int | None = None
    hardware_model: str | None = None
    active_cpus: int | None = None
    memory_gb: int | None = None
    hw_page_size: int | None = None
    vm_page_size: int | None = None
    time_since_boot_sec: int | None = None
    time_awake_since_boot_sec: int | None = None
    total_cpu_time_sec: float | None = None
    total_cycles: int | None = None
    total_instructions: int | None = None
    total_cpi: float | None = None
    memory_pressure_avg_pct: int | None = None
    memory_pressure_max_pct: int | None = None
    available_memory_avg_gb: float | None = None
    available_memory_min_gb: float | None = None
    free_disk_gb: float | None = None
    total_disk_gb: float | None = None
    advisory_battery: int | None = None
    advisory_user: int | None = None
    advisory_thermal: int | None = None
    advisory_combined: int | None = None
    shared_cache_residency_pct: float | None = None
    vnodes_available_pct: float | None = None
    data_source: str | None = None
    reason: str | None = None
    shared_caches: list[TailspinSharedCache] = field(default_factory=list)
    io_stats: list[TailspinIOStats] = field(default_factory=list)


@dataclass
class TailspinData:
    """Complete parsed tailspin data."""

    header: TailspinHeader
    processes: list[TailspinProcess]
    io_histograms: list[TailspinIOHistogramBucket]
    io_aggregates: list[TailspinIOAggregate]


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


def _parse_size(s: str) -> int:
    """Parse size string like '14.83 MB' or '674.97 KB' to bytes."""
    s = s.strip()
    match = re.match(r"([\d.]+)\s*(KB|MB|GB|B)?", s, re.IGNORECASE)
    if not match:
        return 0
    value = float(match.group(1))
    unit = (match.group(2) or "B").upper()
    multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
    return int(value * multipliers.get(unit, 1))


def _parse_count_suffix(s: str) -> int:
    """Parse count with optional suffix like '51.3G' or '87.4G'."""
    s = s.strip()
    match = re.match(r"([\d.]+)([KMGT])?", s, re.IGNORECASE)
    if not match:
        return 0
    value = float(match.group(1))
    suffix = (match.group(2) or "").upper()
    multipliers = {"": 1, "K": 1000, "M": 1_000_000, "G": 1_000_000_000, "T": 1_000_000_000_000}
    return int(value * multipliers.get(suffix, 1))


def _parse_process_ref(s: str) -> tuple[str, int] | None:
    """Parse 'name [pid]' format, return (name, pid) or None."""
    match = re.match(r"(.+?)\s+\[(\d+)\]", s.strip())
    if match:
        return match.group(1), int(match.group(2))
    return None


def parse_tailspin(text: str) -> TailspinData:
    """Parse complete spindump text output from tailspin decode.

    Parses:
    - Header section with system metadata
    - Process blocks with threads and call stacks
    - Binary images per process
    - I/O histograms and aggregate stats at end

    Args:
        text: Raw spindump stdout text from `spindump -i <file> -stdout`

    Returns:
        TailspinData with all parsed information
    """
    lines = text.split("\n")

    # Find where processes start (first "Process:" line)
    process_start_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("Process:"):
            process_start_idx = i
            break

    # Parse header (everything before first Process:)
    header = _parse_header(lines[:process_start_idx])

    # Find where I/O histograms start (after all processes)
    io_start_idx = len(lines)
    for i in range(len(lines) - 1, process_start_idx, -1):
        if lines[i].startswith("IO Size Histogram:"):
            io_start_idx = i
            break

    # Parse processes (between header and I/O section)
    processes = _parse_processes(lines[process_start_idx:io_start_idx])

    # Parse I/O histograms and aggregates
    io_histograms, io_aggregates = _parse_io_section(lines[io_start_idx:])

    return TailspinData(
        header=header,
        processes=processes,
        io_histograms=io_histograms,
        io_aggregates=io_aggregates,
    )


def _parse_header(lines: list[str]) -> TailspinHeader:
    """Parse the header section of spindump output."""
    # Required fields with defaults
    start_time = ""
    end_time = ""
    duration_sec = 0.0
    steps = 0
    sampling_interval_ms = 10
    os_version = ""
    architecture = ""

    # Optional fields
    header_kwargs: dict = {}
    shared_caches: list[TailspinSharedCache] = []
    io_stats: list[TailspinIOStats] = []

    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("Date/Time:"):
            start_time = line.split(":", 1)[1].strip()
        elif line.startswith("End time:"):
            end_time = line.split(":", 1)[1].strip()
        elif line.startswith("Duration:"):
            match = re.search(r"([\d.]+)s", line)
            if match:
                duration_sec = float(match.group(1))
        elif line.startswith("Steps:"):
            match = re.search(r"(\d+)\s*\((\d+)ms", line)
            if match:
                steps = int(match.group(1))
                sampling_interval_ms = int(match.group(2))
        elif line.startswith("OS Version:"):
            os_version = line.split(":", 1)[1].strip()
        elif line.startswith("Architecture:"):
            architecture = line.split(":", 1)[1].strip()
        elif line.startswith("Report Version:"):
            try:
                header_kwargs["report_version"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("Hardware model:"):
            header_kwargs["hardware_model"] = line.split(":", 1)[1].strip()
        elif line.startswith("Active cpus:"):
            try:
                header_kwargs["active_cpus"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("Memory size:"):
            match = re.search(r"(\d+)\s*GB", line)
            if match:
                header_kwargs["memory_gb"] = int(match.group(1))
        elif line.startswith("HW page size:"):
            try:
                header_kwargs["hw_page_size"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("VM page size:"):
            try:
                header_kwargs["vm_page_size"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("Time Since Boot:"):
            match = re.search(r"(\d+)s", line)
            if match:
                header_kwargs["time_since_boot_sec"] = int(match.group(1))
        elif line.startswith("Time Awake Since Boot:"):
            match = re.search(r"(\d+)s", line)
            if match:
                header_kwargs["time_awake_since_boot_sec"] = int(match.group(1))
        elif line.startswith("Total CPU Time:"):
            # Format: 17.620s (51.3G cycles, 87.4G instructions, 0.59c/i)
            match = re.search(
                r"([\d.]+)s\s*\(([\d.]+[KMGT]?)\s*cycles,\s*([\d.]+[KMGT]?)\s*instructions,\s*([\d.]+)c/i\)",
                line,
            )
            if match:
                header_kwargs["total_cpu_time_sec"] = float(match.group(1))
                header_kwargs["total_cycles"] = _parse_count_suffix(match.group(2))
                header_kwargs["total_instructions"] = _parse_count_suffix(match.group(3))
                header_kwargs["total_cpi"] = float(match.group(4))
        elif line.startswith("Memory pressure:"):
            match = re.search(r"average\s*(\d+)%.*highest\s*(\d+)%", line)
            if match:
                header_kwargs["memory_pressure_avg_pct"] = int(match.group(1))
                header_kwargs["memory_pressure_max_pct"] = int(match.group(2))
        elif line.startswith("Available memory:"):
            match = re.search(r"average\s*([\d.]+)\s*GB.*lowest\s*([\d.]+)\s*GB", line)
            if match:
                header_kwargs["available_memory_avg_gb"] = float(match.group(1))
                header_kwargs["available_memory_min_gb"] = float(match.group(2))
        elif line.startswith("Free disk space:"):
            match = re.search(r"([\d.]+)\s*GB/([\d.]+)\s*GB", line)
            if match:
                header_kwargs["free_disk_gb"] = float(match.group(1))
                header_kwargs["total_disk_gb"] = float(match.group(2))
        elif line.startswith("Advisory levels:"):
            # Battery -> 3, User -> 2, ThermalPressure -> 0, Combined -> 2
            for key, field in [
                ("Battery", "advisory_battery"),
                ("User", "advisory_user"),
                ("ThermalPressure", "advisory_thermal"),
                ("Combined", "advisory_combined"),
            ]:
                match = re.search(rf"{key}\s*->\s*(\d+)", line)
                if match:
                    header_kwargs[field] = int(match.group(1))
        elif line.startswith("Shared cache residency:"):
            match = re.search(r"([\d.]+)%", line)
            if match:
                header_kwargs["shared_cache_residency_pct"] = float(match.group(1))
        elif line.startswith("Vnodes Available:"):
            match = re.search(r"([\d.]+)%", line)
            if match:
                header_kwargs["vnodes_available_pct"] = float(match.group(1))
        elif line.startswith("Data Source:"):
            header_kwargs["data_source"] = line.split(":", 1)[1].strip()
        elif line.startswith("Reason:"):
            header_kwargs["reason"] = line.split(":", 1)[1].strip()
        elif line.startswith("Shared Cache:"):
            # UUID slid base address 0xADDR, slide 0xADDR (Name)
            cache_pattern = (
                r"([A-F0-9-]+)\s+slid base address\s+(0x[0-9a-f]+),"
                r"\s*slide\s+(0x[0-9a-f]+)\s*\(([^)]+)\)"
            )
            match = re.search(cache_pattern, line, re.IGNORECASE)
            if match:
                shared_caches.append(
                    TailspinSharedCache(
                        uuid=match.group(1),
                        base_address=match.group(2),
                        slide=match.group(3),
                        name=match.group(4),
                    )
                )
        elif line.startswith("I/O statistics:"):
            # Parse indented I/O stats lines
            j = i + 1
            while j < len(lines) and lines[j].startswith("  "):
                stat_line = lines[j].strip()
                # Overall: 293 IOs (86 IOs/s), 14.83 MB (4471.19 KB/s)
                match = re.match(
                    r"(\w+):\s*(\d+)\s*IOs?\s*\(([\d.]+)\s*IOs?/s\),\s*([\d.]+\s*[KMGB]+)\s*\(([\d.]+)\s*([KMGB]+)/s\)",
                    stat_line,
                    re.IGNORECASE,
                )
                if match:
                    tier = match.group(1).lower()
                    io_stats.append(
                        TailspinIOStats(
                            tier=tier,
                            io_count=int(match.group(2)),
                            io_rate=float(match.group(3)),
                            bytes_total=_parse_size(match.group(4)),
                            bytes_rate=float(match.group(5))
                            * {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}.get(
                                match.group(6).upper(), 1
                            ),
                        )
                    )
                j += 1
            i = j - 1

        i += 1

    return TailspinHeader(
        start_time=start_time,
        end_time=end_time,
        duration_sec=duration_sec,
        steps=steps,
        sampling_interval_ms=sampling_interval_ms,
        os_version=os_version,
        architecture=architecture,
        shared_caches=shared_caches,
        io_stats=io_stats,
        **header_kwargs,
    )


def _parse_processes(lines: list[str]) -> list[TailspinProcess]:
    """Parse all process blocks."""
    processes: list[TailspinProcess] = []
    current_process: TailspinProcess | None = None
    current_thread: TailspinThread | None = None
    in_binary_images = False

    # Regex patterns
    process_pattern = re.compile(r"^Process:\s+(.+?)\s+\[(\d+)\]")
    thread_pattern = re.compile(
        r"^\s{2}Thread\s+(0x[0-9a-f]+)"
        r"(?:\s+DispatchQueue\s+\"([^\"]+)\"\((\d+)\))?"
        r"(?:\s+Thread name\s+\"([^\"]+)\")?"
        r"(?:\s+(\d+)\s+samples?\s*\((\d+)-(\d+)\))?"
        r"(?:\s+priority\s+(\d+)\s*\(base\s+(\d+)\))?"
        r"(?:\s+cpu time\s+([\d.]+)s\s*\(([\d.]+[KMGT]?)\s*cycles,"
        r"\s*([\d.]+[KMGT]?)\s*instructions,\s*([\d.]+)c/i\))?"
        r"(?:\s+(\d+)\s+I/Os?\s*\(([^)]+)\))?",
        re.IGNORECASE,
    )
    frame_pattern = re.compile(
        r"^\s+(\*?)(\d+)\s+"  # optional kernel marker and sample count
        r"(.+?)\s+"  # symbol info
        r"\[(0x[0-9a-f]+)\]"  # address
        r"(?:\s+\(([^)]+)\))?$",  # optional state
        re.IGNORECASE,
    )
    binary_image_pattern = re.compile(
        r"^\s+(\*?)(0x[0-9a-f]+)\s*-\s*(0x[0-9a-f]+|(?:\?\?\?))\s+"
        r"(.+?)\s+"
        r"<([A-F0-9-]+)>"
        r"(?:__TEXT_EXEC)?\s*"
        r"(.*)$",
        re.IGNORECASE,
    )

    i = 0
    while i < len(lines):
        line = lines[i]

        # Check for new process
        proc_match = process_pattern.match(line)
        if proc_match:
            # Save previous process
            if current_process is not None:
                if current_thread is not None:
                    current_process.threads.append(current_thread)
                processes.append(current_process)

            current_process = TailspinProcess(
                pid=int(proc_match.group(2)),
                name=proc_match.group(1),
            )
            current_thread = None
            in_binary_images = False
            i += 1
            continue

        if current_process is None:
            i += 1
            continue

        # Check for Binary Images section
        if line.strip() == "Binary Images:":
            if current_thread is not None:
                current_process.threads.append(current_thread)
                current_thread = None
            in_binary_images = True
            i += 1
            continue

        # Parse binary images
        if in_binary_images:
            bi_match = binary_image_pattern.match(line)
            if bi_match:
                is_kernel = bi_match.group(1) == "*"
                end_addr = bi_match.group(3) if bi_match.group(3) != "???" else None
                name_version = bi_match.group(4).strip()
                path = bi_match.group(6).strip() if bi_match.group(6) else None

                # Parse name and version
                nv_match = re.match(r"(.+?)\s+(\d[\d.]*(?:\s*\([^)]+\))?)\s*$", name_version)
                if nv_match:
                    name = nv_match.group(1).strip()
                    version = nv_match.group(2).strip()
                else:
                    name = name_version
                    version = None

                current_process.binary_images.append(
                    TailspinBinaryImage(
                        start_address=bi_match.group(2),
                        end_address=end_addr,
                        name=name,
                        version=version,
                        uuid=bi_match.group(5),
                        path=path,
                        is_kernel=is_kernel,
                    )
                )
            elif line.strip() and not line.startswith(" "):
                # Non-indented line means we've left binary images
                in_binary_images = False
            i += 1
            continue

        # Parse process metadata
        if line.startswith("UUID:"):
            current_process.uuid = line.split(":", 1)[1].strip()
        elif line.startswith("Path:"):
            current_process.path = line.split(":", 1)[1].strip()
        elif line.startswith("Identifier:"):
            current_process.identifier = line.split(":", 1)[1].strip()
        elif line.startswith("Version:"):
            current_process.version = line.split(":", 1)[1].strip()
        elif line.startswith("Parent:"):
            ref = _parse_process_ref(line.split(":", 1)[1])
            if ref:
                current_process.parent_name, current_process.parent_pid = ref
        elif line.startswith("Responsible:"):
            ref = _parse_process_ref(line.split(":", 1)[1])
            if ref:
                current_process.responsible_name, current_process.responsible_pid = ref
        elif line.startswith("Execed from:"):
            ref = _parse_process_ref(line.split(":", 1)[1])
            if ref:
                current_process.execed_from_name, current_process.execed_from_pid = ref
        elif line.startswith("Execed to:"):
            ref = _parse_process_ref(line.split(":", 1)[1])
            if ref:
                current_process.execed_to_name, current_process.execed_to_pid = ref
        elif line.startswith("Architecture:"):
            current_process.architecture = line.split(":", 1)[1].strip()
        elif line.startswith("Shared Cache:"):
            match = re.search(r"([A-F0-9-]+)\s+slid", line, re.IGNORECASE)
            if match:
                current_process.shared_cache_uuid = match.group(1)
        elif line.startswith("RunningBoard Mgd:"):
            current_process.runningboard_managed = "Yes" in line
        elif line.startswith("Sudden Term:"):
            current_process.sudden_term = line.split(":", 1)[1].strip()
        elif line.startswith("Note:"):
            current_process.notes.append(line.split(":", 1)[1].strip())
        elif line.startswith("Footprint:"):
            # Could be "586.69 MB" or "256 KB -> 11.52 MB (+11.27 MB)"
            match = re.search(r"([\d.]+)\s*(KB|MB|GB)", line)
            if match:
                val = float(match.group(1))
                unit = match.group(2).upper()
                if unit == "KB":
                    val /= 1024
                elif unit == "GB":
                    val *= 1024
                current_process.footprint_mb = val
            # Check for delta
            delta_match = re.search(r"\(\+?([\d.]+)\s*(KB|MB|GB)\)", line)
            if delta_match:
                val = float(delta_match.group(1))
                unit = delta_match.group(2).upper()
                if unit == "KB":
                    val /= 1024
                elif unit == "GB":
                    val *= 1024
                current_process.footprint_delta_mb = val
        elif line.startswith("I/O:"):
            match = re.search(r"(\d+)\s*I/Os?\s*\(([^)]+)\)", line)
            if match:
                current_process.io_count = int(match.group(1))
                current_process.io_bytes = _parse_size(match.group(2))
        elif line.startswith("Time Since Fork:"):
            match = re.search(r"(\d+)s", line)
            if match:
                current_process.time_since_fork_sec = int(match.group(1))
        elif line.startswith("Start time:"):
            current_process.start_time = line.split(":", 1)[1].strip()
        elif line.startswith("End time:") and current_process.start_time:
            # Only for short-lived processes (has start_time)
            current_process.end_time = line.split(":", 1)[1].strip()
        elif line.startswith("Num samples:"):
            # "341 (1-341)" or "0 (task existed only between...)"
            match = re.search(r"(\d+)\s*\((\d+)-(\d+)\)", line)
            if match:
                current_process.num_samples = int(match.group(1))
                current_process.sample_range_start = int(match.group(2))
                current_process.sample_range_end = int(match.group(3))
            else:
                match = re.search(r"(\d+)", line)
                if match:
                    current_process.num_samples = int(match.group(1))
        elif line.startswith("CPU Time:"):
            match = re.search(
                r"([\d.]+)s\s*\(([\d.]+[KMGT]?)\s*cycles,\s*([\d.]+[KMGT]?)\s*instructions,\s*([\d.]+)c/i\)",
                line,
            )
            if match:
                current_process.cpu_time_sec = float(match.group(1))
                current_process.cycles = _parse_count_suffix(match.group(2))
                current_process.instructions = _parse_count_suffix(match.group(3))
                current_process.cpi = float(match.group(4))
            else:
                match = re.search(r"([\d.]+)s", line)
                if match:
                    current_process.cpu_time_sec = float(match.group(1))
        elif line.startswith("Num threads:"):
            match = re.search(r"(\d+)", line)
            if match:
                current_process.num_threads = int(match.group(1))

        # Check for thread
        elif thread_match := thread_pattern.match(line):
            # Save previous thread
            if current_thread is not None:
                current_process.threads.append(current_thread)

            current_thread = TailspinThread(thread_id=thread_match.group(1))

            if thread_match.group(2):
                current_thread.dispatch_queue_name = thread_match.group(2)
            if thread_match.group(3):
                current_thread.dispatch_queue_serial = int(thread_match.group(3))
            if thread_match.group(4):
                current_thread.thread_name = thread_match.group(4)
            if thread_match.group(5):
                current_thread.num_samples = int(thread_match.group(5))
            if thread_match.group(6):
                current_thread.sample_range_start = int(thread_match.group(6))
            if thread_match.group(7):
                current_thread.sample_range_end = int(thread_match.group(7))
            if thread_match.group(8):
                current_thread.priority = int(thread_match.group(8))
            if thread_match.group(9):
                current_thread.base_priority = int(thread_match.group(9))
            if thread_match.group(10):
                current_thread.cpu_time_sec = float(thread_match.group(10))
            if thread_match.group(11):
                current_thread.cycles = _parse_count_suffix(thread_match.group(11))
            if thread_match.group(12):
                current_thread.instructions = _parse_count_suffix(thread_match.group(12))
            if thread_match.group(13):
                current_thread.cpi = float(thread_match.group(13))
            if thread_match.group(14):
                current_thread.io_count = int(thread_match.group(14))
            if thread_match.group(15):
                current_thread.io_bytes = _parse_size(thread_match.group(15))

        # Check for stack frame (only if we have a current thread)
        elif current_thread is not None and (frame_match := frame_pattern.match(line)):
            is_kernel = frame_match.group(1) == "*"
            sample_count = int(frame_match.group(2))
            symbol_info = frame_match.group(3).strip()
            address = frame_match.group(4)
            state_info = frame_match.group(5)

            # Calculate depth from indentation (2 spaces per level, starting at 2)
            indent = len(line) - len(line.lstrip())
            depth = (indent - 2) // 2

            # Parse symbol info: "symbol + offset (library + offset)" or "??? (library + offset)"
            # or "??? [address]" for JIT
            symbol_name = None
            symbol_offset = None
            library_name = None
            library_offset = None

            if symbol_info != "???":
                # Try to parse "symbol + offset (library + offset)"
                sym_match = re.match(r"(.+?)\s*\+\s*(\d+)\s*\((.+?)\s*\+\s*(\d+)\)", symbol_info)
                if sym_match:
                    symbol_name = sym_match.group(1).strip()
                    symbol_offset = int(sym_match.group(2))
                    library_name = sym_match.group(3).strip()
                    library_offset = int(sym_match.group(4))
                else:
                    # Try "??? (library + offset)"
                    lib_match = re.match(r"\?\?\?\s*\((.+?)\s*\+\s*(\d+)\)", symbol_info)
                    if lib_match:
                        library_name = lib_match.group(1).strip()
                        library_offset = int(lib_match.group(2))

            # Parse state
            state = None
            core_type = None
            blocked_on = None
            if state_info:
                if "running" in state_info.lower():
                    state = "running"
                    if "p-core" in state_info.lower():
                        core_type = "p-core"
                    elif "e-core" in state_info.lower():
                        core_type = "e-core"
                elif "blocked by wait4" in state_info.lower():
                    state = "blocked"
                    blocked_pattern = r"blocked by wait4 on\s+(.+)"
                    blocked_match = re.search(blocked_pattern, state_info, re.IGNORECASE)
                    if blocked_match:
                        blocked_on = blocked_match.group(1).strip()

            frame = TailspinFrame(
                sample_count=sample_count,
                is_kernel=is_kernel,
                address=address,
                depth=depth,
                symbol_name=symbol_name,
                symbol_offset=symbol_offset,
                library_name=library_name,
                library_offset=library_offset,
                state=state,
                core_type=core_type,
                blocked_on=blocked_on,
            )
            current_thread.frames.append(frame)

        i += 1

    # Don't forget the last process
    if current_process is not None:
        if current_thread is not None:
            current_process.threads.append(current_thread)
        processes.append(current_process)

    return processes


def _parse_io_section(
    lines: list[str],
) -> tuple[list[TailspinIOHistogramBucket], list[TailspinIOAggregate]]:
    """Parse I/O histograms and aggregate stats at end of file."""
    histograms: list[TailspinIOHistogramBucket] = []
    aggregates: list[TailspinIOAggregate] = []

    current_histogram_type: str | None = None
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        if line == "IO Size Histogram:":
            current_histogram_type = "io_size"
        elif line.endswith("Latency Histogram:"):
            match = re.match(r"Tier\s*(\d+)\s*Latency", line)
            if match:
                current_histogram_type = f"tier{match.group(1)}_latency"
        elif line.endswith("Aggregate Stats:"):
            match = re.match(r"Tier\s*(\d+)\s*Aggregate", line)
            if match:
                tier = f"tier{match.group(1)}"
                # Next line has the stats
                i += 1
                if i < len(lines):
                    stats_line = lines[i].strip()
                    agg = TailspinIOAggregate(tier=tier, num_ios=0)

                    num_match = re.search(r"Num IOs\s*(\d+)", stats_line)
                    if num_match:
                        agg.num_ios = int(num_match.group(1))

                    mean_match = re.search(r"Latency Mean\s*(\d+)us", stats_line)
                    if mean_match:
                        agg.latency_mean_us = int(mean_match.group(1))

                    max_match = re.search(r"Max Latency\s*(\d+)us", stats_line)
                    if max_match:
                        agg.latency_max_us = int(max_match.group(1))

                    sd_match = re.search(r"Latency SD\s*(\d+)us", stats_line)
                    if sd_match:
                        agg.latency_sd_us = int(sd_match.group(1))

                    # Next line has reads/writes
                    i += 1
                    if i < len(lines):
                        rw_line = lines[i].strip()
                        read_match = re.search(r"Reads\s*(\d+)\s*\(([^)]+)\)", rw_line)
                        if read_match:
                            agg.read_count = int(read_match.group(1))
                            agg.read_bytes = _parse_size(read_match.group(2))

                        write_match = re.search(r"Writes\s*(\d+)\s*\(([^)]+)\)", rw_line)
                        if write_match:
                            agg.write_count = int(write_match.group(1))
                            agg.write_bytes = _parse_size(write_match.group(2))

                    aggregates.append(agg)
                current_histogram_type = None
        elif current_histogram_type and line and not line.startswith("Begin"):
            # Parse histogram bucket line
            # Format: "0KB       4KB		     218		     218"
            # or "0us      100us		       46		       46"
            # or ">  1000000us		        0		       55"

            parts = line.split()
            if len(parts) >= 4:
                try:
                    if parts[0] == ">":
                        # Overflow bucket
                        begin_val = int(re.sub(r"[^\d]", "", parts[1]))
                        end_val = None
                        freq = int(parts[2])
                        cdf = int(parts[3])
                    else:
                        begin_val = int(re.sub(r"[^\d]", "", parts[0]))
                        end_val = int(re.sub(r"[^\d]", "", parts[1]))
                        freq = int(parts[2])
                        cdf = int(parts[3])

                    histograms.append(
                        TailspinIOHistogramBucket(
                            histogram_type=current_histogram_type,
                            begin_value=begin_val,
                            end_value=end_val,
                            frequency=freq,
                            cdf=cdf,
                        )
                    )
                except (ValueError, IndexError):
                    pass

        i += 1

    return histograms, aggregates


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

    Raw captures (tailspin, logs) are written to temp directory,
    parsed for insights, and the parsed data is stored in the database.
    Temp files are cleaned up after processing.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        event_id: int,
        runtime_dir: Path,
        log_seconds: int = 60,
    ):
        """Initialize forensics capture.

        Args:
            conn: Database connection
            event_id: The process event ID this capture is associated with
            runtime_dir: Directory for tailspin captures (must match sudoers rule)
            log_seconds: Seconds of logs to capture (default 60)
        """
        self.conn = conn
        self.event_id = event_id
        self._runtime_dir = runtime_dir
        self._log_seconds = log_seconds
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
            f"{self._log_seconds}s",
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
        """Decode tailspin via spindump -i and store ALL data in DB.

        Tailspin files are binary. We decode them using:
            spindump -i <file> -stdout

        This produces text format that we fully parse and store.

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
            # Decode tailspin using spindump (no sudo needed for decode)
            completed = subprocess.run(
                ["/usr/sbin/spindump", "-i", str(result), "-stdout"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                start_new_session=True,
            )

            if completed.returncode != 0:
                log.warning("tailspin_decode_failed", returncode=completed.returncode)
                return "failed"

            text = completed.stdout.decode("utf-8", errors="replace")
            data = parse_tailspin(text)

            # Store header
            insert_tailspin_header(
                self.conn,
                capture_id,
                start_time=data.header.start_time,
                end_time=data.header.end_time,
                duration_sec=data.header.duration_sec,
                steps=data.header.steps,
                sampling_interval_ms=data.header.sampling_interval_ms,
                os_version=data.header.os_version,
                architecture=data.header.architecture,
                report_version=data.header.report_version,
                hardware_model=data.header.hardware_model,
                active_cpus=data.header.active_cpus,
                memory_gb=data.header.memory_gb,
                hw_page_size=data.header.hw_page_size,
                vm_page_size=data.header.vm_page_size,
                time_since_boot_sec=data.header.time_since_boot_sec,
                time_awake_since_boot_sec=data.header.time_awake_since_boot_sec,
                total_cpu_time_sec=data.header.total_cpu_time_sec,
                total_cycles=data.header.total_cycles,
                total_instructions=data.header.total_instructions,
                total_cpi=data.header.total_cpi,
                memory_pressure_avg_pct=data.header.memory_pressure_avg_pct,
                memory_pressure_max_pct=data.header.memory_pressure_max_pct,
                available_memory_avg_gb=data.header.available_memory_avg_gb,
                available_memory_min_gb=data.header.available_memory_min_gb,
                free_disk_gb=data.header.free_disk_gb,
                total_disk_gb=data.header.total_disk_gb,
                advisory_battery=data.header.advisory_battery,
                advisory_user=data.header.advisory_user,
                advisory_thermal=data.header.advisory_thermal,
                advisory_combined=data.header.advisory_combined,
                shared_cache_residency_pct=data.header.shared_cache_residency_pct,
                vnodes_available_pct=data.header.vnodes_available_pct,
                data_source=data.header.data_source,
                reason=data.header.reason,
            )

            # Store shared caches
            for cache in data.header.shared_caches:
                insert_tailspin_shared_cache(
                    self.conn,
                    capture_id,
                    uuid=cache.uuid,
                    base_address=cache.base_address,
                    slide=cache.slide,
                    name=cache.name,
                )

            # Store I/O stats from header
            for io_stat in data.header.io_stats:
                insert_tailspin_io_stats(
                    self.conn,
                    capture_id,
                    tier=io_stat.tier,
                    io_count=io_stat.io_count,
                    bytes_total=io_stat.bytes_total,
                    io_rate=io_stat.io_rate,
                    bytes_rate=io_stat.bytes_rate,
                )

            # Store processes
            total_frames = 0
            for proc in data.processes:
                proc_id = insert_tailspin_process(
                    self.conn,
                    capture_id,
                    proc.pid,
                    proc.name,
                    uuid=proc.uuid,
                    path=proc.path,
                    identifier=proc.identifier,
                    version=proc.version,
                    parent_pid=proc.parent_pid,
                    parent_name=proc.parent_name,
                    responsible_pid=proc.responsible_pid,
                    responsible_name=proc.responsible_name,
                    execed_from_pid=proc.execed_from_pid,
                    execed_from_name=proc.execed_from_name,
                    execed_to_pid=proc.execed_to_pid,
                    execed_to_name=proc.execed_to_name,
                    architecture=proc.architecture,
                    shared_cache_uuid=proc.shared_cache_uuid,
                    runningboard_managed=proc.runningboard_managed,
                    sudden_term=proc.sudden_term,
                    footprint_mb=proc.footprint_mb,
                    footprint_delta_mb=proc.footprint_delta_mb,
                    io_count=proc.io_count,
                    io_bytes=proc.io_bytes,
                    time_since_fork_sec=proc.time_since_fork_sec,
                    start_time=proc.start_time,
                    end_time=proc.end_time,
                    num_samples=proc.num_samples,
                    sample_range_start=proc.sample_range_start,
                    sample_range_end=proc.sample_range_end,
                    cpu_time_sec=proc.cpu_time_sec,
                    cycles=proc.cycles,
                    instructions=proc.instructions,
                    cpi=proc.cpi,
                    num_threads=proc.num_threads,
                )

                # Store process notes
                for note in proc.notes:
                    insert_tailspin_process_note(self.conn, proc_id, note)

                # Store binary images
                for img in proc.binary_images:
                    insert_tailspin_binary_image(
                        self.conn,
                        proc_id,
                        start_address=img.start_address,
                        name=img.name,
                        is_kernel=img.is_kernel,
                        end_address=img.end_address,
                        version=img.version,
                        uuid=img.uuid,
                        path=img.path,
                    )

                # Store threads and frames
                for thread in proc.threads:
                    thread_db_id = insert_tailspin_thread(
                        self.conn,
                        proc_id,
                        thread.thread_id,
                        dispatch_queue_name=thread.dispatch_queue_name,
                        dispatch_queue_serial=thread.dispatch_queue_serial,
                        thread_name=thread.thread_name,
                        num_samples=thread.num_samples,
                        sample_range_start=thread.sample_range_start,
                        sample_range_end=thread.sample_range_end,
                        priority=thread.priority,
                        base_priority=thread.base_priority,
                        cpu_time_sec=thread.cpu_time_sec,
                        cycles=thread.cycles,
                        instructions=thread.instructions,
                        cpi=thread.cpi,
                        io_count=thread.io_count,
                        io_bytes=thread.io_bytes,
                    )

                    # Store frames with parent tracking
                    # Frames are in order by depth, we track parent at each depth level
                    depth_to_frame_id: dict[int, int] = {}

                    for frame in thread.frames:
                        parent_id = (
                            depth_to_frame_id.get(frame.depth - 1) if frame.depth > 0 else None
                        )

                        frame_id = insert_tailspin_frame(
                            self.conn,
                            thread_db_id,
                            frame.depth,
                            frame.sample_count,
                            frame.is_kernel,
                            frame.address,
                            parent_frame_id=parent_id,
                            symbol_name=frame.symbol_name,
                            symbol_offset=frame.symbol_offset,
                            library_name=frame.library_name,
                            library_offset=frame.library_offset,
                            state=frame.state,
                            core_type=frame.core_type,
                            blocked_on=frame.blocked_on,
                        )

                        depth_to_frame_id[frame.depth] = frame_id
                        total_frames += 1

            # Store I/O histograms
            for bucket in data.io_histograms:
                insert_tailspin_io_histogram(
                    self.conn,
                    capture_id,
                    bucket.histogram_type,
                    bucket.begin_value,
                    bucket.frequency,
                    bucket.cdf,
                    bucket.end_value,
                )

            # Store I/O aggregates
            for agg in data.io_aggregates:
                insert_tailspin_io_aggregate(
                    self.conn,
                    capture_id,
                    agg.tier,
                    agg.num_ios,
                    latency_mean_us=agg.latency_mean_us,
                    latency_max_us=agg.latency_max_us,
                    latency_sd_us=agg.latency_sd_us,
                    read_count=agg.read_count,
                    read_bytes=agg.read_bytes,
                    write_count=agg.write_count,
                    write_bytes=agg.write_bytes,
                )

            log.info(
                "tailspin_parsed",
                process_count=len(data.processes),
                thread_count=sum(len(p.threads) for p in data.processes),
                frame_count=total_frames,
            )
            return "success"

        except Exception:
            log.warning("tailspin_decode_failed", exc_info=True)
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

        except Exception:
            log.warning("logs_parse_failed", exc_info=True)
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
