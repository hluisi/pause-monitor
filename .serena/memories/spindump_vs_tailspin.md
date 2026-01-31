# spindump vs tailspin — macOS Diagnostic Tools

## Overview

Both tools capture system state for debugging hangs and pauses, but they answer fundamentally different questions:

- **spindump**: "Where is code executing?" (callstacks, thread states)
- **tailspin**: "What is the kernel doing?" (kdebug events, kernel activity)

For **rogue-hunter's purpose** (diagnosing system pauses), tailspin is more valuable because it captures what happened *during* the freeze. Our daemon is frozen too during a pause — we can't observe it while it happens. Tailspin's rolling buffer is the only way to see inside a system freeze.

| Aspect | **spindump** | **tailspin** |
|--------|--------------|--------------|
| **Purpose** | Sample callstacks of all processes | Capture kernel trace buffer |
| **Operation** | On-demand snapshot | Continuous rolling buffer |
| **Sudo required** | **Yes** (live sampling) | **Yes** (save only) |
| **Capture window** | Configurable (default 10s forward) | ~20 seconds backward |
| **Output** | Text report (.spindump.txt) | Binary file (.tailspin) |

## When to Use Each

| Scenario | Tool | Why |
|----------|------|-----|
| **System pause/freeze** | `sudo tailspin save` | Only tool that captures what happened *during* the freeze |
| **App is unresponsive now** | `sudo spindump <pid>` | Shows where threads are blocked in code |
| **Investigating past pause** | tailspin primarily | Kernel events show root cause; spindump only shows post-freeze state |
| **Kernel-level debugging** | tailspin | kdebug events: scheduler, I/O, interrupts, syscalls |
| **Process-level debugging** | spindump | Detailed per-thread callstacks and blocking info |

**Key insight for rogue-hunter:** When a system pause occurs, our daemon is frozen too. We cannot observe the pause while it's happening. Tailspin's rolling buffer (recording continuously in the kernel) is the only way to see what caused the freeze.

## Why spindump Needs sudo

spindump "samples user and kernel callstacks for every process in the system." Reading memory and stacks from all processes requires root privileges.

```bash
$ spindump -notarget 1 100
spindump must be run as root when sampling the live system
```

## Why tailspin save Needs sudo

While `tailspin enable/disable/info` don't need root, `tailspin save` does:

```bash
$ tailspin save /tmp/test.tailspin
ERROR: must be root to run `tailspin save`.

$ tailspin enable   # This works without sudo
tailspin has been enabled
```

Configuration commands talk to the `tailspind` daemon, but extracting the kernel buffer requires root.

## Commands

### spindump

```bash
# Sample all processes for 10 seconds (default)
sudo spindump

# Sample specific process
sudo spindump <pid> [duration] [interval_ms]
sudo spindump Safari 5 10        # 5 seconds, 10ms interval

# Output options
sudo spindump -o /path/to/output.txt
sudo spindump -stdout -noFile    # Print to stdout only

# Display formats
sudo spindump -heavy             # Sort by count (default)
sudo spindump -timeline          # Sort chronologically

# Parse existing file (no sudo needed)
spindump -i file.tailspin        # Can read tailspin files!
spindump -i file.spindump.txt    # Re-parse spindump output
```

### tailspin

```bash
# Enable/disable continuous tracing
tailspin enable
tailspin disable
tailspin info

# Save buffer to file (requires sudo)
sudo tailspin save /path/to/output.tailspin
sudo tailspin save -r "reason" /path/to/output.tailspin
sudo tailspin save -l 10 /path/to/output.tailspin  # Last 10 seconds

# Augment with symbols/logs
tailspin augment -s file.tailspin
tailspin augment -a file.tailspin  # All augmentation

# View statistics
tailspin stat file.tailspin
```

## Integration Pattern (rogue-hunter)

When a pause is detected (if running privileged):

1. **sudo tailspin save** — Capture kernel activity during the pause (requires root)
2. **spindump -i file.tailspin** — Decode tailspin to readable format (no sudo)
3. **sudo spindump** — Capture current process state if still relevant

**Key insight:** Both `tailspin save` and live `spindump` require sudo. Only parsing existing files (`spindump -i`) works unprivileged.

**Current rogue-hunter status:** Forensics silently fails for tailspin because it doesn't use sudo. Either needs sudoers rules or should skip tailspin entirely when unprivileged.

## Output Locations

| Tool | Default Output |
|------|----------------|
| spindump | `/tmp/<name>_<pid>.spindump.txt` or `/Library/Logs/DiagnosticReports/` |
| tailspin | User-specified path |

## Keyboard Shortcut

**tailspin**: Shift+Control+Option+Command+, (comma) — saves buffer and opens Finder

**spindump**: No shortcut, but Force Quit dialog triggers it automatically for hung apps

## What Each Captures

### tailspin captures (kdebug events):
From Apple's kernel trace system, tailspin records:
- **Scheduler events** — thread scheduling decisions, context switches
- **Interrupts** — hardware interrupt handling
- **System calls** — every syscall entry/exit
- **Mach traps** — kernel IPC operations
- **Mach messages** — inter-process communication
- **File system operations** — reads, writes, vnode ops, journaling
- **Disk I/O** — actual disk read/write events at the block level

This is kernel-level activity that no userspace tool can observe. During a system freeze, this is the only record of what was happening.

### spindump captures:
- User-space callstacks for all threads
- Kernel callstacks
- Thread state (running/blocked/suspended)
- Thread QoS and priority
- Dispatch queue information
- State changes over sample period

### tailspin captures:
- Kernel debug (kdebug) events
- Callstack samples at configured intervals
- System-wide activity in kernel trace buffer
- Events from ~20 seconds before save

## Limitations

| Tool | Limitation |
|------|------------|
| spindump | Cannot see what happened *during* a freeze (it was frozen too) |
| tailspin | Must be enabled beforehand; buffer may not capture everything |
| Both | Symbolication requires matching system/binary versions |

## Decision for rogue-hunter

**We use tailspin only. Live spindump is not used.**

### Rationale

| Data Source | During-Pause Visibility | Post-Pause State |
|-------------|------------------------|------------------|
| Tailspin (decoded via `spindump -i`) | ✓ kernel events, scheduler, I/O | ✓ thread states from buffer |
| Live spindump | ✗ pause already ended | ✓ but it's post-freeze state |

During a system pause, our daemon is frozen too. We can only detect the pause *after* it ends. At that point:
- **Live spindump** shows where threads are *now* (post-recovery) — not useful for root cause
- **Tailspin buffer** captured what the kernel was doing *during* the freeze — this is the valuable data

### Implementation

1. **Privileged**: `sudo tailspin save -o <path>` — captures kernel trace buffer
2. **Unprivileged**: `spindump -i <file> -stdout` — decodes tailspin to readable format
3. **Unprivileged**: `log show --style ndjson` — extracts system logs

### Sudoers Rule

Single rule in `/etc/sudoers.d/rogue-hunter`:
```bash
<user> ALL = (root) NOPASSWD: /usr/bin/tailspin save -o /Users/<user>/.local/share/rogue-hunter/events/*
```

This restricts tailspin to only write to the events directory, limiting blast radius of any accidents.

## Source

- `man spindump` (macOS)
- `man tailspin` (macOS)
- Verified on macOS 15 (Sequoia)
