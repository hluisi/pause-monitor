---
id: audit-issue-finder-iss-017
type: audit
domain: project
subject: issue-finder-iss-017
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: [issue-finder-ISS-017]
tags: []
related: []
sources: []
---

# ISS-017: Exception handlers lose traceback information

**Category:** Error Handling
**All Categories:** Error Handling, Error Information Loss
**Severity:** Important
**Status:** resolved
**Created:** 2026-02-02T12:00:00Z
**Last validated:** 2026-02-02T12:00:00Z

## Grouped Findings

This issue contains 3 related findings:

| # | Category | File | Line | Symbol | Description |
|---|----------|------|------|--------|-------------|
| 1 | Error Handling | daemon.py | 606 | _main_loop | Catch Exception, logs only str(e) |
| 2 | Error Handling | forensics.py | 537 | _process_tailspin | Catch Exception, logs only str(e) |
| 3 | Error Handling | forensics.py | 579 | _process_logs | Catch Exception, logs only str(e) |

## Investigation

### Trace Up

| Symbol | File:Line | Relationship | Notes |
|--------|-----------|--------------|-------|
| Daemon/start | daemon.py:179-240 | Caller | Awaits _main_loop |
| run_daemon | daemon.py:628-640 | Entry point | Has log.exception() (correct) |
| ForensicsCapture/capture_and_store | forensics.py:324-389 | Caller | Calls _process_tailspin and _process_logs |

### Trace Down

| Symbol | File:Line | Relationship | Notes |
|--------|-----------|--------------|-------|
| LibprocCollector/collect | collector.py | Called | ctypes/libproc errors possible |
| subprocess.run | stdlib | Called by forensics | OSError, FileNotFoundError |
| sqlite3 operations | storage.py | Called | sqlite3.Error, IntegrityError |

### Related Patterns

Two existing uses of `log.exception()` in daemon.py (lines 144, 633) show the correct pattern is known but inconsistently applied.

## Root Cause

**Inconsistent error handling patterns**: Developers know `log.exception()` captures tracebacks but don't consistently apply it. The simpler `log.warning("...", error=str(e))` pattern loses:
- Exception type (was it `sqlite3.IntegrityError` or `OSError`?)
- Full traceback (where did it originate?)
- Chained exceptions (`__cause__` or `__context__`)

## Suggestions

### 1. For _main_loop (daemon.py:606)

```python
except Exception as e:
    log.exception("sample_failed")  # Captures full traceback
    # Or: log.warning("sample_failed", exc_info=True)
```

### 2. For _process_tailspin (forensics.py:537)

```python
except Exception as e:
    log.warning("tailspin_decode_failed", exc_info=True)
    return "failed"
```

Or more specific exception handling:
```python
except (OSError, subprocess.CalledProcessError) as e:
    log.warning("tailspin_decode_failed", exc_info=True)
    return "failed"
except sqlite3.Error as e:
    log.warning("tailspin_db_failed", exc_info=True)
    return "failed"
```

### 3. For _process_logs (forensics.py:579)

```python
except Exception as e:
    log.warning("logs_parse_failed", exc_info=True)
    return "failed"
```

### 4. Code cleanup

Existing `log.exception()` calls pass redundant `error=str(e)`. Remove since exception info is already captured:

```python
# Before
log.exception("daemon_crashed", error=str(e))
# After
log.exception("daemon_crashed")
```

## Notes

- structlog is configured with `format_exc_info` processor, so `exc_info=True` works
- For forensics capture (best-effort), continuing after error may be acceptable, but traceback should be preserved for debugging
- Specific exceptions matter: FileNotFoundError vs PermissionError vs sqlite3.IntegrityError all have different meanings

## Resolution

**Resolved:** 2026-02-02

**Changed to use `exc_info=True`:**
- `daemon.py` `_main_loop`: Added `log.warning("sample_failed", exc_info=True)`
- `forensics.py` `_process_tailspin`: `log.warning("tailspin_decode_failed", exc_info=True)`
- `forensics.py` `_process_logs`: `log.warning("logs_parse_failed", exc_info=True)`

**Removed redundant `error=str(e)` from `log.exception()` calls:**
- `daemon.py` `_trigger_forensics_capture`: `log.exception("forensics_callback_failed", ...)`
- `daemon.py` `run_daemon`: `log.exception("daemon_crashed")`

**Cleanup:** Changed `except Exception as e:` to `except Exception:` where `e` was no longer used (linter-enforced).

All 375 tests pass.
