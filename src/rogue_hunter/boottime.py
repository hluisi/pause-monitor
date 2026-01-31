"""Boot time detection for macOS."""

import re
import subprocess


def get_boot_time() -> int:
    """Return system boot time as Unix timestamp."""
    result = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True)
    match = re.search(r"sec = (\d+)", result.stdout)
    if match:
        return int(match.group(1))
    raise RuntimeError("Failed to parse boot time from sysctl")
