"""IOKit interface for macOS GPU metrics.

Uses ctypes to call IOKit/CoreFoundation directly - no subprocess overhead.

This module provides access to per-process GPU time via AGXDeviceUserClient
entries in IORegistry. Most processes have 0 GPU time; only GPU-using apps
(WindowServer, browsers, games) will have entries.

All functions handle errors gracefully by returning empty results.
"""

import ctypes
import re
from ctypes import POINTER, byref, c_int, c_uint32, c_void_p

# ─────────────────────────────────────────────────────────────────────────────
# Library loading
# ─────────────────────────────────────────────────────────────────────────────

try:
    _iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit", use_errno=True)
    _cf = ctypes.CDLL(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
        use_errno=True,
    )
    _IOKIT_AVAILABLE = True
except OSError:
    _IOKIT_AVAILABLE = False
    _iokit = None
    _cf = None


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

kIOMainPortDefault = 0
kCFStringEncodingUTF8 = 0x08000100
kIORegistryIterateRecursively = 1


# ─────────────────────────────────────────────────────────────────────────────
# Type aliases for readability
# ─────────────────────────────────────────────────────────────────────────────

mach_port_t = c_uint32
io_iterator_t = c_uint32
io_object_t = c_uint32
io_registry_entry_t = c_uint32
CFTypeRef = c_void_p
CFStringRef = c_void_p
CFDictionaryRef = c_void_p
CFArrayRef = c_void_p
CFNumberRef = c_void_p
CFIndex = ctypes.c_long


# ─────────────────────────────────────────────────────────────────────────────
# Function signatures
# ─────────────────────────────────────────────────────────────────────────────

if _IOKIT_AVAILABLE and _iokit and _cf:
    # IOKit functions
    _iokit.IOServiceGetMatchingServices.argtypes = [
        mach_port_t,
        CFDictionaryRef,
        POINTER(io_iterator_t),
    ]
    _iokit.IOServiceGetMatchingServices.restype = c_int

    _iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
    _iokit.IOServiceMatching.restype = CFDictionaryRef

    _iokit.IOIteratorNext.argtypes = [io_iterator_t]
    _iokit.IOIteratorNext.restype = io_object_t

    _iokit.IOObjectRelease.argtypes = [io_object_t]
    _iokit.IOObjectRelease.restype = c_int

    _iokit.IORegistryEntryCreateCFProperty.argtypes = [
        io_registry_entry_t,
        CFStringRef,
        c_void_p,  # CFAllocatorRef
        c_uint32,  # IOOptionBits
    ]
    _iokit.IORegistryEntryCreateCFProperty.restype = CFTypeRef

    _iokit.IORegistryEntryGetChildIterator.argtypes = [
        io_registry_entry_t,
        ctypes.c_char_p,  # plane name
        POINTER(io_iterator_t),
    ]
    _iokit.IORegistryEntryGetChildIterator.restype = c_int

    _iokit.IOObjectGetClass.argtypes = [io_object_t, ctypes.c_char_p]
    _iokit.IOObjectGetClass.restype = c_int

    # CoreFoundation functions
    _cf.CFStringCreateWithCString.argtypes = [c_void_p, ctypes.c_char_p, c_uint32]
    _cf.CFStringCreateWithCString.restype = CFStringRef

    _cf.CFStringGetCString.argtypes = [
        CFStringRef,
        ctypes.c_char_p,
        CFIndex,
        c_uint32,
    ]
    _cf.CFStringGetCString.restype = ctypes.c_bool

    _cf.CFStringGetLength.argtypes = [CFStringRef]
    _cf.CFStringGetLength.restype = CFIndex

    _cf.CFArrayGetCount.argtypes = [CFArrayRef]
    _cf.CFArrayGetCount.restype = CFIndex

    _cf.CFArrayGetValueAtIndex.argtypes = [CFArrayRef, CFIndex]
    _cf.CFArrayGetValueAtIndex.restype = c_void_p

    _cf.CFDictionaryGetValue.argtypes = [CFDictionaryRef, c_void_p]
    _cf.CFDictionaryGetValue.restype = c_void_p

    _cf.CFNumberGetValue.argtypes = [CFNumberRef, c_int, c_void_p]
    _cf.CFNumberGetValue.restype = ctypes.c_bool

    _cf.CFRelease.argtypes = [CFTypeRef]
    _cf.CFRelease.restype = None

    _cf.CFGetTypeID.argtypes = [CFTypeRef]
    _cf.CFGetTypeID.restype = ctypes.c_ulong

    _cf.CFStringGetTypeID.argtypes = []
    _cf.CFStringGetTypeID.restype = ctypes.c_ulong

    _cf.CFArrayGetTypeID.argtypes = []
    _cf.CFArrayGetTypeID.restype = ctypes.c_ulong

    _cf.CFDictionaryGetTypeID.argtypes = []
    _cf.CFDictionaryGetTypeID.restype = ctypes.c_ulong

    _cf.CFNumberGetTypeID.argtypes = []
    _cf.CFNumberGetTypeID.restype = ctypes.c_ulong


# CFNumber type constants
kCFNumberSInt64Type = 4


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────


def _cfstr(s: str) -> CFStringRef:
    """Create a CFString from a Python string."""
    return _cf.CFStringCreateWithCString(None, s.encode("utf-8"), kCFStringEncodingUTF8)


def _cfstr_to_str(cfstr: CFStringRef) -> str | None:
    """Convert a CFString to a Python string."""
    if not cfstr:
        return None
    length = _cf.CFStringGetLength(cfstr)
    # UTF-8 can use up to 4 bytes per character
    buf_size = length * 4 + 1
    buf = ctypes.create_string_buffer(buf_size)
    if _cf.CFStringGetCString(cfstr, buf, buf_size, kCFStringEncodingUTF8):
        return buf.value.decode("utf-8")
    return None


def _get_cf_number_int64(cfnum: CFNumberRef) -> int | None:
    """Extract an int64 value from a CFNumber."""
    if not cfnum:
        return None
    value = ctypes.c_int64()
    if _cf.CFNumberGetValue(cfnum, kCFNumberSInt64Type, byref(value)):
        return value.value
    return None


def _is_cf_string(ref: CFTypeRef) -> bool:
    """Check if a CFTypeRef is a CFString."""
    return _cf.CFGetTypeID(ref) == _cf.CFStringGetTypeID()


def _is_cf_array(ref: CFTypeRef) -> bool:
    """Check if a CFTypeRef is a CFArray."""
    return _cf.CFGetTypeID(ref) == _cf.CFArrayGetTypeID()


def _is_cf_dict(ref: CFTypeRef) -> bool:
    """Check if a CFTypeRef is a CFDictionary."""
    return _cf.CFGetTypeID(ref) == _cf.CFDictionaryGetTypeID()


def _is_cf_number(ref: CFTypeRef) -> bool:
    """Check if a CFTypeRef is a CFNumber."""
    return _cf.CFGetTypeID(ref) == _cf.CFNumberGetTypeID()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

# Regex to extract PID from IOUserClientCreator strings like "pid 410, WindowServer"
_PID_PATTERN = re.compile(r"pid\s+(\d+)")


def get_gpu_usage() -> dict[int, int]:
    """Get per-process GPU time from IORegistry.

    Finds AGXAccelerator service and iterates its AGXDeviceUserClient children
    to find GPU usage per process. Each GPU-using process has an entry with:
    - IOUserClientCreator: "pid 410, WindowServer"
    - AppUsage: [{accumulatedGPUTime: nanoseconds, ...}]

    Note: AGXDeviceUserClient entries are not registered services, so we must
    find them via the AGXAccelerator parent rather than IOServiceMatching.

    Returns:
        Dictionary mapping PID to cumulative GPU time in nanoseconds.
        Returns empty dict if IOKit unavailable or on error.

    Note:
        Most processes have 0 GPU time (no entry). Only GPU-using processes
        like WindowServer, browsers, games will have entries. The returned
        values are cumulative since process start.
    """
    if not _IOKIT_AVAILABLE or not _iokit or not _cf:
        return {}

    result: dict[int, int] = {}

    # Find AGXAccelerator (the GPU device) - this IS a registered service
    matching = _iokit.IOServiceMatching(b"AGXAccelerator")
    if not matching:
        return {}

    iterator = io_iterator_t()
    kr = _iokit.IOServiceGetMatchingServices(kIOMainPortDefault, matching, byref(iterator))
    # Note: IOServiceMatching dict is consumed by IOServiceGetMatchingServices
    if kr != 0:
        return {}

    try:
        # Create CFStrings for property keys (reuse across iterations)
        creator_key = _cfstr("IOUserClientCreator")
        usage_key = _cfstr("AppUsage")
        gpu_time_key = _cfstr("accumulatedGPUTime")

        try:
            # Iterate through all AGXAccelerator entries (usually just one)
            while True:
                accelerator = _iokit.IOIteratorNext(iterator)
                if not accelerator:
                    break

                try:
                    # Get child iterator for the IOService plane
                    child_iterator = io_iterator_t()
                    kr = _iokit.IORegistryEntryGetChildIterator(
                        accelerator, b"IOService", byref(child_iterator)
                    )
                    if kr != 0 or not child_iterator.value:
                        continue

                    try:
                        # Iterate through children looking for AGXDeviceUserClient
                        while True:
                            child = _iokit.IOIteratorNext(child_iterator)
                            if not child:
                                break

                            try:
                                # Check if this child is AGXDeviceUserClient
                                classname = ctypes.create_string_buffer(128)
                                _iokit.IOObjectGetClass(child, classname)
                                if classname.value != b"AGXDeviceUserClient":
                                    continue

                                # Get IOUserClientCreator property
                                creator_ref = _iokit.IORegistryEntryCreateCFProperty(
                                    child, creator_key, None, 0
                                )
                                if not creator_ref:
                                    continue

                                try:
                                    if not _is_cf_string(creator_ref):
                                        continue
                                    creator_str = _cfstr_to_str(creator_ref)
                                    if not creator_str:
                                        continue

                                    # Extract PID from creator string
                                    match = _PID_PATTERN.search(creator_str)
                                    if not match:
                                        continue
                                    pid = int(match.group(1))

                                    # Get AppUsage array
                                    usage_ref = _iokit.IORegistryEntryCreateCFProperty(
                                        child, usage_key, None, 0
                                    )
                                    if not usage_ref:
                                        continue

                                    try:
                                        if not _is_cf_array(usage_ref):
                                            continue

                                        count = _cf.CFArrayGetCount(usage_ref)
                                        if count == 0:
                                            continue

                                        # Sum up all GPU times in the array
                                        total_gpu_time = 0
                                        for i in range(count):
                                            usage_dict = _cf.CFArrayGetValueAtIndex(usage_ref, i)
                                            if not usage_dict or not _is_cf_dict(usage_dict):
                                                continue

                                            gpu_time_ref = _cf.CFDictionaryGetValue(
                                                usage_dict, gpu_time_key
                                            )
                                            if gpu_time_ref and _is_cf_number(gpu_time_ref):
                                                gpu_time = _get_cf_number_int64(gpu_time_ref)
                                                if gpu_time is not None:
                                                    total_gpu_time += gpu_time

                                        if total_gpu_time > 0:
                                            # A process might have multiple entries
                                            # (multiple GPU contexts), so accumulate
                                            result[pid] = result.get(pid, 0) + total_gpu_time

                                    finally:
                                        _cf.CFRelease(usage_ref)
                                finally:
                                    _cf.CFRelease(creator_ref)
                            finally:
                                _iokit.IOObjectRelease(child)
                    finally:
                        _iokit.IOObjectRelease(child_iterator)
                finally:
                    _iokit.IOObjectRelease(accelerator)

        finally:
            _cf.CFRelease(creator_key)
            _cf.CFRelease(usage_key)
            _cf.CFRelease(gpu_time_key)

    finally:
        _iokit.IOObjectRelease(iterator)

    return result
