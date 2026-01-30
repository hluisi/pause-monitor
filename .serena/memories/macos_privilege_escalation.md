# macOS Privilege Escalation for Monitoring Tools

Research on best practices for running applications that need elevated permissions on macOS, specifically for tools like `tailspin save` and `spindump` that require root.

## The Problem

pause-monitor needs root privileges for:
- `tailspin save` — extracts kernel trace buffer (requires root)
- `spindump` — samples all process callstacks (requires root)

**Note:** Process scheduling priority does NOT require root. Use macOS QoS classes via `pthread_set_qos_class_self_np` instead of `nice -10`. See `daemon.py` for implementation.

But running the entire daemon as root causes issues:
- Files created with root ownership in wrong locations
- `~` expands to `/var/root` instead of user's home
- TCC dialogs don't appear for root daemons (silent permission failures)
- Config paths resolve incorrectly
- Elevated attack surface

**Apple's principle**: "Use privileged processes only for those operations that really require elevated privileges."

## Approaches

### 1. Sudoers Rules (Recommended for CLI Tools)

Add NOPASSWD rules for specific commands in `/etc/sudoers.d/`:

```bash
# /etc/sudoers.d/pause-monitor
%admin ALL = (root) NOPASSWD: /usr/bin/tailspin save *
%admin ALL = (root) NOPASSWD: /usr/sbin/spindump -notarget *
```

**Security requirements**:
- Always use **full absolute paths** (prevents PATH hijacking)
- Be specific about allowed arguments (avoid broad wildcards)
- Use `visudo` to edit (validates syntax before saving)
- Store in `/etc/sudoers.d/` with mode `0440` owner `root:wheel`

**Implementation**:
```python
# Use sudo -n (non-interactive) to fail fast if password required
cmd = ["/usr/bin/sudo", "-n", "/usr/bin/tailspin", "save", "-o", str(output_path)]
```

**Pros**: Simple, legitimate for developer tools, no code signing needed
**Cons**: Requires manual setup or installer, user must be admin

### 2. Privileged Helper LaunchDaemon

Run a minimal separate daemon as root that handles only privileged operations:

```
User Session                          System
┌─────────────────────────┐          ┌─────────────────────────┐
│ pause-monitor daemon    │   IPC    │ pause-monitor-helper    │
│ (user privileges)       │ ──────── │ (root via LaunchDaemon) │
│ - metrics, storage      │  socket  │ - tailspin save         │
│ - pause detection       │          │ - spindump              │
└─────────────────────────┘          └─────────────────────────┘
```

LaunchDaemon plist goes in `/Library/LaunchDaemons/`.

**Pros**: Clean privilege separation, main app runs as user
**Cons**: Two processes, IPC complexity, installation requires admin

### 3. SMAppService / SMJobBless (Apple's Official Way)

Use Authorization Services + SMJobBless to install privileged helper with verified code signing.

**When to use**: 
- macOS 13+: SMAppService
- Older systems: SMJobBless

**Pros**: Apple-recommended, secure trust chain, code signing verification
**Cons**: Requires Swift/Obj-C, complex setup, impractical for Python CLI tools

### 4. Run Entire Daemon as Root (Anti-pattern)

Run everything via root LaunchDaemon in `/Library/LaunchDaemons/`.

**If you must**:
1. Use explicit paths (never rely on `~`)
2. Create files then `chown` to correct user
3. Use `$SUDO_USER` to get original username
4. Drop privileges for non-privileged operations

**Cons**: 
- TCC dialogs don't appear for root daemons
- Files created as root
- Large attack surface
- Path resolution issues

## Security Gotchas

| Gotcha | Mitigation |
|--------|------------|
| `~` expands to `/var/root` when root | Use explicit paths or `os.path.expanduser()` with `$SUDO_USER` |
| PATH hijacking via Homebrew | Always use absolute paths in sudoers AND subprocess calls |
| TCC blocks root daemons silently | Don't run GUI-dependent or TCC-protected code as root |
| Wildcards in sudoers | Be specific: `/usr/bin/tailspin save *` not `/usr/bin/tailspin *` |
| Files created as root | `chown` after creation or run file ops as user |
| sudo credential caching (5 min) | Not an issue with NOPASSWD rules |
| setuid executables | **Never use setuid**—use launchd instead (Apple mandate) |

## Getting the Original User

When running via sudo, get the real user:

```python
import os

def get_real_user() -> str:
    """Get the actual user, even when running as root via sudo."""
    return os.environ.get("SUDO_USER") or os.environ.get("USER") or "unknown"

def get_real_home() -> Path:
    """Get the real user's home directory."""
    user = get_real_user()
    if user and user != "root":
        return Path(f"/Users/{user}")
    return Path.home()
```

## Decision for pause-monitor

**Approach:** Single sudoers rule for `tailspin save` only.

**Why:**
- Personal project — no App Store, no code signing requirements
- Only one command needs root (`tailspin save`)
- Live spindump dropped (tailspin decode provides same data)
- Narrow rule limits blast radius of accidents

**Sudoers rule** (`/etc/sudoers.d/pause-monitor`):
```bash
<user> ALL = (root) NOPASSWD: /usr/bin/tailspin save -o /Users/<user>/.local/share/pause-monitor/events/*
```

**Implementation:**
1. `pause-monitor install` creates the sudoers file (requires one-time sudo)
2. Forensics uses `sudo -n /usr/bin/tailspin save -o <path>`
3. If `sudo -n` fails, forensics errors loudly (no silent fallback)

**Why not SMJobBless?** Designed for Swift/Obj-C apps with code signing. For Python CLI tools, sudoers is the pragmatic choice—even Apple's `powermetrics` requires `sudo`.

## Example: Checking Sudo Availability

```python
async def check_sudo_available(command: str) -> bool:
    """Check if we can run a command via sudo without password."""
    proc = await asyncio.create_subprocess_exec(
        "/usr/bin/sudo", "-n", "-l", command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return proc.returncode == 0
```

## Sources

- [Apple: Elevating Privileges Safely](https://developer.apple.com/library/archive/documentation/Security/Conceptual/SecureCodingGuide/Articles/AccessControl.html)
- [Apple Developer Forums: BSD Privilege Escalation](https://developer.apple.com/forums/thread/708765)
- [macOS Daemonology (XPC and agents)](https://medium.com/@alkenso/macos-daemonology-d471fd21edd2)
- [Creating XPC Services](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingXPCServices.html)
- [nixCraft: sudo without password](https://www.cyberciti.biz/faq/linux-unix-running-sudo-command-without-a-password/)
- [Jamf: macOS Performance Monitoring](https://www.jamf.com/blog/macos-performance-monitoring-collection/)
