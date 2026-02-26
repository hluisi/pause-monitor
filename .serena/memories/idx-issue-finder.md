# Issue Finder Index

**Last updated:** 2026-02-02
**Total active:** 0

## Issues

| ID | Title | Category | Severity | Status | Findings |
|----|-------|----------|----------|--------|----------|
| ISS-001 | Unused TUI instance variables (_process_count, _sample_count) | Unnecessary Code | Minor | resolved | 2 |
| ISS-002 | Legacy tier system remnants throughout codebase | Unnecessary Code | Important | resolved | 3 |
| ISS-003 | Unused _run_heavy_capture method in Daemon | Unnecessary Code | Important | resolved | 1 |
| ISS-004 | Unused get_core_count function | Unnecessary Code | Minor | resolved | 1 |
| ISS-005 | Unused SuspectsConfig patterns (YAGNI) | Unnecessary Code | Important | resolved | 1 |
| ISS-006 | Unused Notifier._critical_start_time | Unnecessary Code | Minor | resolved | 1 |
| ISS-007 | Stub action_show_history in TUI | Unnecessary Code | Minor | resolved | 1 |
| ISS-008 | CLI database check + connection boilerplate duplication | Duplication | Important | resolved | 1 |
| ISS-009 | Config.save() overly long with repetitive serialization | Complexity | Important | resolved | 1 |
| ISS-014 | Missing error handling in boottime.get_boot_time | Error Handling | Important | resolved | 1 |
| ISS-015 | Unused NormalizationConfig and config fields | Unnecessary Code | Important | resolved | 4 |
| ISS-016 | Root privileges/SUDO_USER validation repeated in CLI | Duplication | Important | resolved | 5 |
| ISS-017 | Exception handlers lose traceback information | Error Handling | Important | resolved | 3 |

## Summary

| Severity | Active | Resolved |
|----------|--------|----------|
| Important | 0 | 9 |
| Minor | 0 | 4 |

| Category | Active | Resolved |
|----------|--------|----------|
| Unnecessary Code | 0 | 7 |
| Duplication | 0 | 2 |
| Complexity | 0 | 1 |
| Error Handling | 0 | 2 |
| Structure | 0 | 0 |

## Detailed Memories

Issues with full investigation notes (use `read_memory`):
- `issue-finder-ISS-002` - Legacy tier system (resolved)
- `issue-finder-ISS-008` - CLI duplication (resolved)
- `issue-finder-ISS-009` - Config.save() complexity (resolved)
- `issue-finder-ISS-014` - boottime error handling (resolved - rewritten with ctypes)
- `issue-finder-ISS-015` - Unused config fields (active)
- `issue-finder-ISS-016` - CLI privilege validation (active)
- `issue-finder-ISS-017` - Exception traceback loss (active)

## Recently Resolved (this scan)

| ID | Title | Resolution |
|----|-------|------------|
| ISS-001 | Unused TUI instance variables | Now used in _update_gauge() |
| ISS-003 | Unused _run_heavy_capture method | Removed from codebase |
| ISS-004 | Unused get_core_count function | Removed from codebase |
| ISS-005 | Unused SuspectsConfig patterns | Removed from codebase |
| ISS-006 | Unused Notifier._critical_start_time | Removed from codebase |
| ISS-014 | Missing error handling in boottime | Rewritten with ctypes sysctlbyname |

**Next ID:** ISS-018
