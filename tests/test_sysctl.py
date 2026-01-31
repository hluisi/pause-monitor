"""Tests for sysctl module."""

from rogue_hunter.sysctl import sysctl_int


def test_sysctl_int_returns_none_for_invalid():
    """Invalid sysctl names return None."""
    assert sysctl_int("this.does.not.exist") is None
