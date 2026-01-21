"""Stress sentinel with tiered monitoring.

Fast loop (100ms): load, memory, I/O via sysctl/IOKit
Slow loop (1s): GPU, wakeups, thermal via powermetrics
"""

import os

from pause_monitor.sysctl import sysctl_int


def collect_fast_metrics() -> dict:
    """Collect fast-path metrics (~20us).

    Uses sysctl and os.getloadavg() - no subprocess calls.
    """
    load_avg = os.getloadavg()[0]  # 1-minute average
    memory_pressure = sysctl_int("kern.memorystatus_level")  # 0-100
    page_free_count = sysctl_int("vm.page_free_count")

    return {
        "load_avg": load_avg,
        "memory_pressure": memory_pressure,
        "page_free_count": page_free_count,
    }
