"""Low-level sysctl interface for macOS system metrics.

Uses ctypes to call sysctlbyname() directly - no subprocess overhead (~20us).
"""

import ctypes
from ctypes import byref, c_int, c_int64, c_size_t

# Load libc for sysctl access
# Note: Using CDLL(None) works on macOS/Linux. This module is macOS-only.
libc = ctypes.CDLL(None)
libc.sysctlbyname.argtypes = [
    ctypes.c_char_p,
    ctypes.c_void_p,
    ctypes.POINTER(c_size_t),
    ctypes.c_void_p,
    c_size_t,
]
libc.sysctlbyname.restype = c_int


def sysctl_int(name: str) -> int | None:
    """Read an integer sysctl value by MIB name.

    Args:
        name: sysctl MIB name (e.g., "kern.memorystatus_level")

    Returns:
        Integer value on success, None if sysctl doesn't exist or fails.

    Note:
        Uses c_int64 buffer which handles both 32-bit and 64-bit sysctls.
        The actual value size doesn't matter since sysctl only writes
        what it needs. This avoids a double syscall to detect type.
    """
    value = c_int64()
    size = c_size_t(ctypes.sizeof(value))
    result = libc.sysctlbyname(name.encode(), byref(value), byref(size), None, 0)
    return value.value if result == 0 else None
