# ISS-016: Root privileges/SUDO_USER validation repeated in CLI

**Category:** Duplication
**All Categories:** Duplication, Error Handling Boilerplate
**Severity:** Important
**Status:** resolved
**Created:** 2026-02-02T12:00:00Z
**Last validated:** 2026-02-02T12:00:00Z

## Grouped Findings

This issue contains 5 related findings:

| # | Category | File | Line | Symbol | Description |
|---|----------|------|------|--------|-------------|
| 1 | Duplication | cli.py | 530-537 | perms_install | Root check + SUDO_USER validation |
| 2 | Duplication | cli.py | 564-566 | perms_uninstall | Root check only |
| 3 | Duplication | cli.py | 659-670 | service_install | Conditional root + SUDO_USER |
| 4 | Duplication | cli.py | 761-772 | service_uninstall | Conditional root + SUDO_USER |
| 5 | Duplication | cli.py | 821-824 | service_status | Username determination only |

## Investigation

### Trace Up

| Symbol | File:Line | Relationship | Notes |
|--------|-----------|--------------|-------|
| perms (group) | cli.py:512 | Parent | Groups perms_install, perms_uninstall |
| service (group) | cli.py:638 | Parent | Groups service commands |
| main (cli) | cli.py:61 | Root | Entry point |

### Trace Down

| Symbol | File:Line | Relationship | Notes |
|--------|-----------|--------------|-------|
| _setup_sudoers | cli.py:490-509 | Called | Uses username from SUDO_USER check |
| _get_service_paths | cli.py:622-639 | Called | Requires username |
| launchctl | subprocess | Called | Requires proper user context |

### Related Patterns

Two distinct validation patterns exist:
1. **Unconditional root requirement** (perms_install, perms_uninstall)
2. **Conditional root requirement** (service_install, service_uninstall based on --system flag)

## Root Cause

**Organic growth**: Each command was written independently, copying existing patterns rather than extracting common logic. No shared utility exists despite Click supporting callbacks and decorators.

## Suggestions

**Recommended: Helper function approach**

```python
def get_effective_username(require_root: bool = False) -> str:
    """Get the effective username, validating root if required.
    
    Args:
        require_root: If True, fail if not running as root.
        
    Returns:
        The username to use for operations.
        
    Raises:
        SystemExit: If root is required but not available, or if SUDO_USER
            cannot be determined when running as root.
    """
    import os
    
    if require_root and os.getuid() != 0:
        click.echo("Error: requires root privileges. Use sudo.", err=True)
        raise SystemExit(1)
    
    if os.getuid() == 0:
        username = os.environ.get("SUDO_USER")
        if not username:
            click.echo("Error: Could not determine user. Run with sudo, not as root.", err=True)
            raise SystemExit(1)
        return username
    else:
        return os.environ.get("USER")
```

Usage:
```python
@service.command("install")
@click.option("--system", "system_wide", is_flag=True)
def service_install(system_wide: bool, force: bool) -> None:
    username = get_effective_username(require_root=system_wide)
    ...
```

## Notes

- `perms_uninstall` is inconsistent: checks for root but does NOT validate SUDO_USER, unlike other functions
- Current inline checks are difficult to test; a helper function can be unit tested
- Also consolidate `sudoers_path = Path("/etc/sudoers.d/rogue-hunter")` repeated 3 times into a constant

## Resolution

**Resolved:** 2026-02-02

Created `get_effective_username(require_root: bool = False) -> str` helper that:
- Validates root privileges when `require_root=True`
- Returns SUDO_USER when running as root, USER otherwise
- Provides consistent error messages across all commands

Also added `SUDOERS_PATH` module constant.

Updated commands:
- `perms_install` — uses helper (require_root=True)
- `perms_uninstall` — uses helper (also fixes the missing SUDO_USER check bug)
- `service_install` — uses helper (require_root=system_wide)
- `service_uninstall` — uses helper (require_root=system_wide)
- `_setup_sudoers`, `perms_status` — use SUDOERS_PATH constant

All 375 tests pass.
