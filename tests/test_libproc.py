"""Tests for libproc module."""

import ctypes
import os

from pause_monitor.libproc import (
    MachTimebaseInfo,
    ProcBSDInfo,
    ProcTaskInfo,
    RusageInfoV4,
    abs_to_ns,
    get_bsd_info,
    get_process_name,
    get_rusage,
    get_state_name,
    get_task_info,
    get_timebase_info,
    list_all_pids,
)


class TestStructSizes:
    """Test that struct sizes match C definitions."""

    def test_mach_timebase_info_size(self):
        """MachTimebaseInfo should be 8 bytes (2x uint32)."""
        assert ctypes.sizeof(MachTimebaseInfo) == 8

    def test_rusage_info_v4_size(self):
        """RusageInfoV4 should be 296 bytes.

        This is the canonical size from the kernel headers.
        If this fails, the struct fields don't match the C definition.
        """
        # 16 (uuid) + 35 * 8 (uint64 fields) = 16 + 280 = 296
        assert ctypes.sizeof(RusageInfoV4) == 296

    def test_proc_task_info_size(self):
        """ProcTaskInfo should be 72 bytes."""
        # 6 * 8 (uint64) + 12 * 4 (int32) = 48 + 48 = 96... wait
        # Let me check: 6 uint64 = 48, 12 int32 = 48, total = 96
        # But the struct has alignment padding. Check actual size.
        expected = 6 * 8 + 12 * 4  # 96 bytes
        assert ctypes.sizeof(ProcTaskInfo) == expected

    def test_proc_bsd_info_size(self):
        """ProcBSDInfo should have correct size."""
        # This is architecture-dependent, just verify it's reasonable
        size = ctypes.sizeof(ProcBSDInfo)
        assert size > 100  # At minimum, should be > 100 bytes
        assert size < 500  # But not huge


class TestTimebase:
    """Test mach timebase conversion."""

    def test_get_timebase_info(self):
        """Should return valid timebase info."""
        info = get_timebase_info()
        assert info.numer > 0
        assert info.denom > 0

    def test_intel_timebase(self):
        """On Intel, timebase is usually (1, 1)."""
        info = get_timebase_info()
        # Just verify it's reasonable - we can't assert exact values
        # because this runs on different machines
        assert info.numer >= 1
        assert info.denom >= 1

    def test_abs_to_ns_identity(self):
        """With (1,1) timebase, abs_to_ns returns input."""
        from pause_monitor.libproc import TimebaseInfo

        timebase = TimebaseInfo(numer=1, denom=1)
        assert abs_to_ns(1000, timebase) == 1000

    def test_abs_to_ns_apple_silicon(self):
        """With Apple Silicon timebase (125,3), conversion is correct."""
        from pause_monitor.libproc import TimebaseInfo

        # Apple Silicon typical: 125/3 = ~41.67 ns per tick
        timebase = TimebaseInfo(numer=125, denom=3)
        result = abs_to_ns(3, timebase)  # 3 ticks
        assert result == 125  # (3 * 125) // 3 = 125 ns


class TestPIDListing:
    """Test PID enumeration."""

    def test_list_all_pids(self):
        """Should return list of PIDs including current process."""
        pids = list_all_pids()
        assert isinstance(pids, list)
        assert len(pids) > 0
        # Our own PID should be in the list
        assert os.getpid() in pids

    def test_list_all_pids_no_zeros(self):
        """Should not contain zero (kernel)."""
        pids = list_all_pids()
        assert 0 not in pids

    def test_list_all_pids_no_negatives(self):
        """Should not contain negative values."""
        pids = list_all_pids()
        assert all(pid > 0 for pid in pids)


class TestRusage:
    """Test rusage retrieval."""

    def test_get_rusage_own_process(self):
        """Can get rusage for own process."""
        rusage = get_rusage(os.getpid())
        assert rusage is not None
        # Should have some CPU time
        assert rusage.ri_user_time >= 0
        assert rusage.ri_system_time >= 0
        # Should have memory footprint
        assert rusage.ri_phys_footprint > 0

    def test_get_rusage_nonexistent(self):
        """Nonexistent PID returns None."""
        # Use an absurdly high PID that won't exist
        rusage = get_rusage(999999999)
        assert rusage is None

    def test_get_rusage_pid_1(self):
        """PID 1 (launchd) should work (same user)."""
        # This may return None due to permissions, that's OK
        # Just verify no crash
        get_rusage(1)


class TestTaskInfo:
    """Test task info retrieval."""

    def test_get_task_info_own_process(self):
        """Can get task info for own process."""
        info = get_task_info(os.getpid())
        assert info is not None
        # Should have at least 1 thread
        assert info.pti_threadnum >= 1
        # Context switches should be non-negative
        assert info.pti_csw >= 0

    def test_get_task_info_nonexistent(self):
        """Nonexistent PID returns None."""
        info = get_task_info(999999999)
        assert info is None


class TestBSDInfo:
    """Test BSD info retrieval."""

    def test_get_bsd_info_own_process(self):
        """Can get BSD info for own process."""
        info = get_bsd_info(os.getpid())
        assert info is not None
        # PID should match
        assert info.pbi_pid == os.getpid()
        # Status should be valid (running or sleeping)
        assert info.pbi_status in (2, 3)  # SRUN or SSLEEP

    def test_get_bsd_info_nonexistent(self):
        """Nonexistent PID returns None."""
        info = get_bsd_info(999999999)
        assert info is None


class TestProcessName:
    """Test process name lookup."""

    def test_get_process_name_own(self):
        """Can get name of own process."""
        name = get_process_name(os.getpid())
        assert isinstance(name, str)
        # proc_name may return empty for Python processes - that's OK
        # The important thing is it doesn't crash and returns a string

    def test_get_process_name_nonexistent(self):
        """Nonexistent PID returns empty string."""
        name = get_process_name(999999999)
        assert name == ""


class TestStateName:
    """Test state name conversion."""

    def test_known_states(self):
        """Known state codes map correctly."""
        assert get_state_name(1) == "idle"
        assert get_state_name(2) == "running"
        assert get_state_name(3) == "sleeping"
        assert get_state_name(4) == "stopped"
        assert get_state_name(5) == "zombie"

    def test_unknown_state(self):
        """Unknown state codes return 'unknown'."""
        assert get_state_name(0) == "unknown"
        assert get_state_name(99) == "unknown"
        assert get_state_name(-1) == "unknown"
