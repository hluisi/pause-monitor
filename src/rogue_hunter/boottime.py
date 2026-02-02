"""Boot time detection for macOS.

Uses ctypes to call sysctlbyname() directly — no subprocess overhead.
"""

import ctypes
from ctypes import Structure, byref, c_int, c_long, c_size_t


class Timeval(Structure):
    """struct timeval from sys/time.h."""

    _fields_ = [
        ("tv_sec", c_long),  # seconds
        ("tv_usec", c_long),  # microseconds
    ]


# Load libc for sysctl access
_libc = ctypes.CDLL(None)
_libc.sysctlbyname.argtypes = [
    ctypes.c_char_p,
    ctypes.c_void_p,
    ctypes.POINTER(c_size_t),
    ctypes.c_void_p,
    c_size_t,
]
_libc.sysctlbyname.restype = c_int


def get_boot_time() -> int:
    """Return system boot time as Unix timestamp.

    Uses sysctlbyname() via ctypes for direct kernel access (~20μs).

    Raises:
        RuntimeError: If sysctl call fails.
    """
    tv = Timeval()
    size = c_size_t(ctypes.sizeof(tv))
    result = _libc.sysctlbyname(b"kern.boottime", byref(tv), byref(size), None, 0)
    if result != 0:
        raise RuntimeError("Failed to read kern.boottime via sysctl")
    return tv.tv_sec
