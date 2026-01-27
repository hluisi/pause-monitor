"""Boot time detection for macOS."""

import os


def get_boot_time() -> int:
    """Return system boot time as Unix timestamp."""
    return int(os.stat("/var/run").st_birthtime)
