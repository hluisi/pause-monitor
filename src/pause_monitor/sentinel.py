"""Stress sentinel with tiered monitoring.

Fast loop (100ms): load, memory, I/O via sysctl/IOKit
Slow loop (1s): GPU, wakeups, thermal via powermetrics
"""

import ctypes
import os
from ctypes import byref, c_int, c_int64, c_size_t

# sysctl interface
libc = ctypes.CDLL(None)
libc.sysctlbyname.argtypes = [
    ctypes.c_char_p,
    ctypes.c_void_p,
    ctypes.POINTER(c_size_t),
    ctypes.c_void_p,
    c_size_t,
]
libc.sysctlbyname.restype = c_int


def _sysctl_int(name: str) -> int | None:
    """Read an integer sysctl value."""
    value = c_int64()
    size = c_size_t(ctypes.sizeof(value))
    result = libc.sysctlbyname(name.encode(), byref(value), byref(size), None, 0)
    return value.value if result == 0 else None


def collect_fast_metrics() -> dict:
    """Collect fast-path metrics (~20us).

    Uses sysctl and os.getloadavg() - no subprocess calls.
    """
    load_avg = os.getloadavg()[0]  # 1-minute average
    memory_pressure = _sysctl_int("kern.memorystatus_level")  # 0-100
    page_free_count = _sysctl_int("vm.page_free_count")

    return {
        "load_avg": load_avg,
        "memory_pressure": memory_pressure,
        "page_free_count": page_free_count,
    }
