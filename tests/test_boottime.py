"""Tests for boot time detection."""

import time


def test_get_boot_time_returns_int():
    """get_boot_time() returns boot timestamp as int."""
    from pause_monitor.boottime import get_boot_time

    boot_time = get_boot_time()
    assert isinstance(boot_time, int)
    assert boot_time > 0


def test_get_boot_time_is_stable():
    """get_boot_time() returns same value on repeated calls."""
    from pause_monitor.boottime import get_boot_time

    t1 = get_boot_time()
    t2 = get_boot_time()
    assert t1 == t2


def test_get_boot_time_is_in_past():
    """Boot time should be before now."""
    from pause_monitor.boottime import get_boot_time

    boot_time = get_boot_time()
    assert boot_time < time.time()
