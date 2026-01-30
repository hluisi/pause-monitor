"""Low-level libproc interface for macOS process metrics.

Uses ctypes to call libproc.dylib directly - no subprocess overhead.

This module provides access to:
- proc_pid_rusage: Rich resource usage (CPU time, memory, disk I/O, energy)
- proc_pidinfo: Task info (context switches, syscalls, threads)
- proc_pidinfo: BSD info (process state, command)
- proc_listallpids: List all PIDs
- proc_name: Process name lookup

All functions handle process disappearance gracefully by returning None.
"""

import ctypes
from ctypes import POINTER, Structure, byref, c_char, c_int, c_int32, c_uint8, c_uint32, c_uint64
from dataclasses import dataclass

# ─────────────────────────────────────────────────────────────────────────────
# Library loading
# ─────────────────────────────────────────────────────────────────────────────

libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
libc = ctypes.CDLL(None, use_errno=True)  # For mach_timebase_info

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# proc_pid_rusage flavors
RUSAGE_INFO_V4 = 4

# proc_pidinfo flavors
PROC_PIDTASKINFO = 4
PROC_PIDTBSDINFO = 3

# Buffer sizes
PROC_PIDPATHINFO_MAXSIZE = 4096
MAXCOMLEN = 16

# Process states (from bsd/sys/proc.h)
# These map to pbi_status values
SIDL = 1  # Process being created
SRUN = 2  # Currently runnable
SSLEEP = 3  # Sleeping on an address
SSTOP = 4  # Process stopped
SZOMB = 5  # Awaiting collection by parent

STATE_NAMES = {
    SIDL: "idle",
    SRUN: "running",
    SSLEEP: "sleeping",
    SSTOP: "stopped",
    SZOMB: "zombie",
}


# ─────────────────────────────────────────────────────────────────────────────
# Structures
# ─────────────────────────────────────────────────────────────────────────────


class MachTimebaseInfo(Structure):
    """mach_timebase_info for converting mach_absolute_time to nanoseconds."""

    _fields_ = [
        ("numer", c_uint32),
        ("denom", c_uint32),
    ]


class RusageInfoV4(Structure):
    """rusage_info_v4 from sys/resource.h.

    This is the richest single API call for process metrics.
    """

    _fields_ = [
        # Identification
        ("ri_uuid", c_uint8 * 16),
        # CPU time (in mach_absolute_time units on Apple Silicon!)
        ("ri_user_time", c_uint64),
        ("ri_system_time", c_uint64),
        # Wakeups
        ("ri_pkg_idle_wkups", c_uint64),
        ("ri_interrupt_wkups", c_uint64),
        # Memory
        ("ri_pageins", c_uint64),
        ("ri_wired_size", c_uint64),
        ("ri_resident_size", c_uint64),
        ("ri_phys_footprint", c_uint64),
        ("ri_proc_start_abstime", c_uint64),
        ("ri_proc_exit_abstime", c_uint64),
        # Children
        ("ri_child_user_time", c_uint64),
        ("ri_child_system_time", c_uint64),
        ("ri_child_pkg_idle_wkups", c_uint64),
        ("ri_child_interrupt_wkups", c_uint64),
        ("ri_child_pageins", c_uint64),
        ("ri_child_elapsed_abstime", c_uint64),
        # Disk I/O
        ("ri_diskio_bytesread", c_uint64),
        ("ri_diskio_byteswritten", c_uint64),
        # CPU performance counters
        ("ri_cpu_time_qos_default", c_uint64),
        ("ri_cpu_time_qos_maintenance", c_uint64),
        ("ri_cpu_time_qos_background", c_uint64),
        ("ri_cpu_time_qos_utility", c_uint64),
        ("ri_cpu_time_qos_legacy", c_uint64),
        ("ri_cpu_time_qos_user_initiated", c_uint64),
        ("ri_cpu_time_qos_user_interactive", c_uint64),
        # Energy
        ("ri_billed_system_time", c_uint64),
        ("ri_serviced_system_time", c_uint64),
        # Logical writes
        ("ri_logical_writes", c_uint64),
        # Memory high watermarks
        ("ri_lifetime_max_phys_footprint", c_uint64),
        ("ri_instructions", c_uint64),
        ("ri_cycles", c_uint64),
        ("ri_billed_energy", c_uint64),
        ("ri_serviced_energy", c_uint64),
        ("ri_interval_max_phys_footprint", c_uint64),
        ("ri_runnable_time", c_uint64),
    ]


class ProcTaskInfo(Structure):
    """proc_taskinfo from sys/proc_info.h.

    Contains context switches, syscall counts, thread info.
    """

    _fields_ = [
        ("pti_virtual_size", c_uint64),
        ("pti_resident_size", c_uint64),
        ("pti_total_user", c_uint64),
        ("pti_total_system", c_uint64),
        ("pti_threads_user", c_uint64),
        ("pti_threads_system", c_uint64),
        ("pti_policy", c_int32),
        ("pti_faults", c_int32),
        ("pti_pageins", c_int32),
        ("pti_cow_faults", c_int32),
        ("pti_messages_sent", c_int32),
        ("pti_messages_received", c_int32),
        ("pti_syscalls_mach", c_int32),
        ("pti_syscalls_unix", c_int32),
        ("pti_csw", c_int32),
        ("pti_threadnum", c_int32),
        ("pti_numrunning", c_int32),
        ("pti_priority", c_int32),
    ]


class ProcBSDInfo(Structure):
    """proc_bsdinfo from sys/proc_info.h.

    Contains process state, command name.
    """

    _fields_ = [
        ("pbi_flags", c_uint32),
        ("pbi_status", c_uint32),
        ("pbi_xstatus", c_uint32),
        ("pbi_pid", c_uint32),
        ("pbi_ppid", c_uint32),
        ("pbi_uid", c_uint32),
        ("pbi_gid", c_uint32),
        ("pbi_ruid", c_uint32),
        ("pbi_rgid", c_uint32),
        ("pbi_svuid", c_uint32),
        ("pbi_svgid", c_uint32),
        ("pbi_rfu_1", c_uint32),
        ("pbi_comm", c_char * MAXCOMLEN),
        ("pbi_name", c_char * (2 * MAXCOMLEN)),
        ("pbi_nfiles", c_uint32),
        ("pbi_pgid", c_uint32),
        ("pbi_pjobc", c_uint32),
        ("pbi_e_tdev", c_uint32),
        ("pbi_e_tpgid", c_uint32),
        ("pbi_nice", c_int32),
        ("pbi_start_tvsec", c_uint64),
        ("pbi_start_tvusec", c_uint64),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Function signatures
# ─────────────────────────────────────────────────────────────────────────────

# int proc_pid_rusage(pid_t pid, int flavor, rusage_info_t *buffer)
libproc.proc_pid_rusage.argtypes = [c_int, c_int, ctypes.c_void_p]
libproc.proc_pid_rusage.restype = c_int

# int proc_pidinfo(pid_t pid, int flavor, uint64_t arg, void *buffer, int buffersize)
libproc.proc_pidinfo.argtypes = [c_int, c_int, c_uint64, ctypes.c_void_p, c_int]
libproc.proc_pidinfo.restype = c_int

# int proc_listallpids(void *buffer, int buffersize)
libproc.proc_listallpids.argtypes = [ctypes.c_void_p, c_int]
libproc.proc_listallpids.restype = c_int

# int proc_name(int pid, void *buffer, uint32_t buffersize)
libproc.proc_name.argtypes = [c_int, ctypes.c_void_p, c_uint32]
libproc.proc_name.restype = c_int

# kern_return_t mach_timebase_info(mach_timebase_info_t info)
libc.mach_timebase_info.argtypes = [POINTER(MachTimebaseInfo)]
libc.mach_timebase_info.restype = c_int


# ─────────────────────────────────────────────────────────────────────────────
# Time conversion (Apple Silicon)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class TimebaseInfo:
    """Mach timebase info for converting absolute time to nanoseconds."""

    numer: int
    denom: int


def get_timebase_info() -> TimebaseInfo:
    """Get mach_timebase_info for time conversion.

    Returns:
        TimebaseInfo with numer/denom for conversion.

    Note:
        Intel: (1, 1) - mach_absolute_time is already nanoseconds
        Apple Silicon: (125, 3) - ~41.67ns per tick
    """
    info = MachTimebaseInfo()
    libc.mach_timebase_info(byref(info))
    return TimebaseInfo(numer=info.numer, denom=info.denom)


def abs_to_ns(abstime: int, timebase: TimebaseInfo) -> int:
    """Convert mach_absolute_time to nanoseconds.

    Args:
        abstime: Time in mach_absolute_time units
        timebase: TimebaseInfo from get_timebase_info()

    Returns:
        Time in nanoseconds
    """
    return (abstime * timebase.numer) // timebase.denom


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def list_all_pids() -> list[int]:
    """List all process IDs.

    Returns:
        List of PIDs currently on the system.
    """
    # First call with NULL to get buffer size
    count = libproc.proc_listallpids(None, 0)
    if count <= 0:
        return []

    # Allocate buffer and get PIDs
    buffer = (c_int * count)()
    actual_count = libproc.proc_listallpids(buffer, ctypes.sizeof(buffer))
    if actual_count <= 0:
        return []

    # Filter out zeros (buffer may be larger than needed)
    return [pid for pid in buffer[:actual_count] if pid > 0]


def get_rusage(pid: int) -> RusageInfoV4 | None:
    """Get resource usage for a process.

    Args:
        pid: Process ID

    Returns:
        RusageInfoV4 on success, None if process doesn't exist or permission denied.
    """
    rusage = RusageInfoV4()
    result = libproc.proc_pid_rusage(pid, RUSAGE_INFO_V4, byref(rusage))
    return rusage if result == 0 else None


def get_task_info(pid: int) -> ProcTaskInfo | None:
    """Get task info for a process.

    Args:
        pid: Process ID

    Returns:
        ProcTaskInfo on success, None if process doesn't exist or permission denied.
    """
    info = ProcTaskInfo()
    result = libproc.proc_pidinfo(pid, PROC_PIDTASKINFO, 0, byref(info), ctypes.sizeof(info))
    return info if result > 0 else None


def get_bsd_info(pid: int) -> ProcBSDInfo | None:
    """Get BSD info for a process.

    Args:
        pid: Process ID

    Returns:
        ProcBSDInfo on success, None if process doesn't exist or permission denied.
    """
    info = ProcBSDInfo()
    result = libproc.proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, byref(info), ctypes.sizeof(info))
    return info if result > 0 else None


def get_process_name(pid: int) -> str:
    """Get process name.

    Args:
        pid: Process ID

    Returns:
        Process name, or empty string if not found.
    """
    buffer = ctypes.create_string_buffer(MAXCOMLEN)
    result = libproc.proc_name(pid, buffer, MAXCOMLEN)
    if result > 0:
        return buffer.value.decode("utf-8", errors="replace")
    return ""


def get_state_name(status: int) -> str:
    """Convert process status code to name.

    Args:
        status: pbi_status value from ProcBSDInfo

    Returns:
        State name (idle, running, sleeping, stopped, zombie, unknown)
    """
    return STATE_NAMES.get(status, "unknown")
