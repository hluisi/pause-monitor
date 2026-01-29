# Issue Finder Index

**Last updated:** 2026-01-29
**Total active:** 6

## Issues

| ID | Title | Category | Severity | Status | Findings |
|----|-------|----------|----------|--------|----------|
| ISS-001 | Unused TUI instance variables (_process_count, _sample_count) | Unnecessary Code | Minor | active | 2 |
| ISS-002 | Legacy tier system remnants throughout codebase | Unnecessary Code | Important | resolved | 3 |
| ISS-003 | Unused _run_heavy_capture method in Daemon | Unnecessary Code | Important | active | 1 |
| ISS-004 | Unused get_core_count function | Unnecessary Code | Minor | active | 1 |
| ISS-005 | Unused SuspectsConfig patterns (YAGNI) | Unnecessary Code | Important | active | 1 |
| ISS-006 | Unused Notifier._critical_start_time | Unnecessary Code | Minor | active | 1 |
| ISS-007 | Stub action_show_history in TUI | Unnecessary Code | Minor | resolved | 1 |
| ISS-008 | CLI database check + connection boilerplate duplication | Duplication | Important | resolved | 1 |
| ISS-009 | Config.save() overly long with repetitive serialization | Complexity | Important | resolved | 1 |
| ISS-014 | Missing error handling in boottime.get_boot_time | Error Handling | Important | active | 1 |

## Summary

| Severity | Active | Resolved |
|----------|--------|----------|
| Important | 3 | 3 |
| Minor | 3 | 1 |

| Category | Active | Resolved |
|----------|--------|----------|
| Unnecessary Code | 5 | 2 |
| Complexity | 0 | 1 |
| Error Handling | 1 | 0 |
| Duplication | 0 | 1 |

## Detailed Memories

Issues with full investigation notes (use `read_memory`):
- `issue-finder-ISS-002` - Legacy tier system (resolved)
- `issue-finder-ISS-008` - CLI duplication (resolved)
- `issue-finder-ISS-009` - Config.save() complexity (resolved)
- `issue-finder-ISS-014` - boottime error handling (active)

**Next ID:** ISS-015
