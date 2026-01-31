# ISS-014: Missing error handling in boottime.get_boot_time

**Category:** Error Handling
**All Categories:** Error Handling
**Severity:** Important
**Status:** active
**Created:** 2026-01-29T10:30:00Z
**Last validated:** 2026-01-29T10:30:00Z

## Grouped Findings

| # | Category | File | Line | Symbol | Description |
|---|----------|------|------|--------|-------------|
| 1 | Error Handling | boottime.py | 7-13 | get_boot_time | No subprocess error handling |

## Investigation

### Current Code

```python
def get_boot_time() -> int:
    result = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True)
    match = re.search(r"sec = (\d+)", result.stdout)
    if match:
        return int(match.group(1))
    raise RuntimeError("Failed to parse boot time from sysctl")
```

### Callers (none handle exceptions)

| Caller | File:Line | Impact if Exception |
|--------|-----------|---------------------|
| status() | cli.py:53 | CLI crashes with traceback |
| events() | cli.py:103 | CLI crashes with traceback |
| Daemon.__init__() | daemon.py:65 | Daemon fails to start |
| EventsScreen._refresh_events() | tui/app.py:381 | TUI crashes |
| RogueHunterApp._refresh_events() | tui/app.py:681 | TUI crashes |

### Unhandled Exceptions

- `FileNotFoundError` - sysctl binary missing
- `PermissionError` - execution denied
- `OSError` - various OS-level errors
- Non-zero exit code silently ignored (no `check=True`)

## Root Cause

The function lacks defensive error handling:
1. No return code check - `subprocess.run()` doesn't use `check=True`
2. No exception handling for subprocess failures
3. Callers don't expect failures - CLI try/finally only closes connections

## Suggestions

**Fix in get_boot_time() itself:**

```python
def get_boot_time() -> int:
    try:
        result = subprocess.run(
            ["sysctl", "-n", "kern.boottime"],
            capture_output=True, text=True, timeout=5,
        )
    except FileNotFoundError:
        raise RuntimeError("sysctl command not found - is this macOS?") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("sysctl command timed out") from None
    except OSError as e:
        raise RuntimeError(f"Failed to run sysctl: {e}") from e
    
    if result.returncode != 0:
        raise RuntimeError(f"sysctl failed: {result.stderr}")
    
    match = re.search(r"sec = (\d+)", result.stdout)
    if match:
        return int(match.group(1))
    raise RuntimeError(f"Failed to parse boot time: {result.stdout!r}")
```

## Notes

- Tests only cover happy path, not error conditions
- macOS-specific code - consider platform check
- Low probability but high impact (daemon won't start, TUI crashes)
