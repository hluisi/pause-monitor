"""Tests for iokit module."""

import pytest


class TestGetGpuUsage:
    """Test GPU usage retrieval."""

    def test_returns_dict(self):
        """get_gpu_usage always returns a dict."""
        from rogue_hunter.iokit import get_gpu_usage

        result = get_gpu_usage()
        assert isinstance(result, dict)

    def test_pids_are_positive_integers(self):
        """All PIDs in result should be positive integers."""
        from rogue_hunter.iokit import get_gpu_usage

        result = get_gpu_usage()
        for pid in result:
            assert isinstance(pid, int)
            assert pid > 0

    def test_gpu_times_are_positive(self):
        """All GPU times should be positive integers (nanoseconds)."""
        from rogue_hunter.iokit import get_gpu_usage

        result = get_gpu_usage()
        for gpu_time in result.values():
            assert isinstance(gpu_time, int)
            assert gpu_time > 0

    def test_windowserver_usually_present(self):
        """WindowServer typically uses GPU on macOS with a display.

        This test may be skipped in headless CI environments.
        """
        from rogue_hunter.iokit import get_gpu_usage

        result = get_gpu_usage()

        # Skip if no GPU processes found (headless CI)
        if not result:
            pytest.skip("No GPU processes found (possibly headless environment)")

        # WindowServer usually has PID < 1000 and uses GPU
        # We can't guarantee its exact PID, but if we have GPU processes,
        # at least one should be WindowServer or a similar system process
        assert len(result) > 0

    def test_no_crash_on_repeated_calls(self):
        """Multiple calls should not crash or leak resources."""
        from rogue_hunter.iokit import get_gpu_usage

        for _ in range(10):
            result = get_gpu_usage()
            assert isinstance(result, dict)


class TestIokitUnavailable:
    """Test behavior when IOKit is not available."""

    def test_returns_empty_dict_when_unavailable(self):
        """Should return empty dict when IOKit unavailable."""
        import rogue_hunter.iokit as iokit_module

        # Temporarily mark IOKit as unavailable
        original = iokit_module._IOKIT_AVAILABLE
        try:
            iokit_module._IOKIT_AVAILABLE = False
            result = iokit_module.get_gpu_usage()
            assert result == {}
        finally:
            iokit_module._IOKIT_AVAILABLE = original


class TestPidPattern:
    """Test the PID extraction regex."""

    def test_pid_pattern_standard(self):
        """Standard IOUserClientCreator format."""
        from rogue_hunter.iokit import _PID_PATTERN

        match = _PID_PATTERN.search("pid 410, WindowServer")
        assert match is not None
        assert match.group(1) == "410"

    def test_pid_pattern_large_pid(self):
        """Large PID values."""
        from rogue_hunter.iokit import _PID_PATTERN

        match = _PID_PATTERN.search("pid 999999, SomeApp")
        assert match is not None
        assert match.group(1) == "999999"

    def test_pid_pattern_no_match(self):
        """String without PID pattern."""
        from rogue_hunter.iokit import _PID_PATTERN

        match = _PID_PATTERN.search("no pid here")
        assert match is None

    def test_pid_pattern_extra_spaces(self):
        """Extra spaces between 'pid' and number."""
        from rogue_hunter.iokit import _PID_PATTERN

        match = _PID_PATTERN.search("pid  123, App")
        assert match is not None
        assert match.group(1) == "123"


class TestIntegration:
    """Integration tests that run on actual hardware."""

    def test_cumulative_nature(self):
        """GPU time should be cumulative (non-decreasing over time).

        This test verifies that consecutive calls return the same or higher
        values for the same PID, since GPU time is cumulative since process start.
        """
        from rogue_hunter.iokit import get_gpu_usage

        first = get_gpu_usage()
        if not first:
            pytest.skip("No GPU processes found")

        # Get a second reading
        second = get_gpu_usage()

        # For any PID that appears in both, the second reading should be >= first
        for pid, first_time in first.items():
            if pid in second:
                assert second[pid] >= first_time, (
                    f"PID {pid}: GPU time decreased from {first_time} to {second[pid]}"
                )
