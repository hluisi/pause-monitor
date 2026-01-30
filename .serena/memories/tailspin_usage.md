# tailspin — macOS Diagnostic Tracing Tool

## Overview

`tailspin` configures macOS to continuously sample callstacks and kdebug events in a kernel trace buffer. When saved, it captures approximately **20 seconds** of system state leading up to the save — ideal for post-mortem analysis of system pauses and hangs.

The `tailspind` daemon handles actual tracing in the background (do not run manually).

## Privilege Requirements

| Command | Sudo Required |
|---------|---------------|
| `enable`, `disable`, `info`, `set`, `reset` | No |
| `save` | **Yes** |
| `augment`, `stat` | No (operates on existing files) |

The configuration commands don't need root, but extracting data from the kernel buffer does.

## Commands

### Status & Control

```bash
tailspin info      # Show current configuration
tailspin enable    # Enable collection (persists across reboots)
tailspin disable   # Stop collection (persists across reboots)
tailspin reset     # Reset all settings to defaults
tailspin reset buffer-size  # Reset specific setting
```

### Saving Traces (requires sudo)

```bash
sudo tailspin save /path/to/output.tailspin
sudo tailspin save -r "reason string" /path/to/output.tailspin  # Include reason
sudo tailspin save -l 10 /path/to/output.tailspin               # Last 10 seconds only
sudo tailspin save -n /path/to/output.tailspin                  # Skip symbolication
```

### Augmenting & Analyzing (no sudo needed)

```bash
tailspin augment -s file.tailspin   # Add symbols (same device/build)
tailspin augment -l file.tailspin   # Add logs
tailspin augment -a file.tailspin   # Add all available augmentation
tailspin augment -d -s file.tailspin  # For files from different device

tailspin stat file.tailspin         # Print aggregate info
tailspin stat -v file.tailspin      # Include layout info
tailspin stat -s file.tailspin      # Sort by frequency
```

### Configuration (no sudo needed)

```bash
tailspin set buffer-size 128                          # Buffer size in MB
tailspin set oncore-sampling-period 2000000           # Sample on-CPU threads (ns, min 1ms)
tailspin set oncore-sampling-period disabled          # Disable on-core sampling
tailspin set full-system-sampling-period 50000000     # Sample all threads (ns, min 10ms)
tailspin set full-system-sampling-period disabled     # Disable full-system sampling
tailspin set ktrace-filter-descriptor add:C1,C0x25    # Add event filters
tailspin set sampling-option add:syscall-sampling     # Add sampling options
```

## Keyboard Shortcut

When enabled, capture immediately with:
**Shift + Control + Option + Command + , (comma)**

A Finder window opens with the saved file.

## Defaults (as of macOS 15)

- Buffer size: 100 MB
- Full-system sampling: 10 ms
- On-core sampling: disabled (redundant when full-system enabled)

## Filter Descriptions

Comma-separated class/subclass specifiers:
- Class: `C<byte>` (e.g., `C1`, `C0x25`)
- Subclass: `S<two-bytes>` (e.g., `S0x0521`)
- All events: `ALL`

Example: `C1,C0x25,S0x0521,S0x0523`

## Sampling Options

- `cswitch-sampling` — Sample on context switches
- `syscall-sampling` — Sample on system calls  
- `vmfault-sampling` — Sample on VM faults

## Viewing Tailspin Data

| Tool | Usage |
|------|-------|
| **Instruments.app** | Primary GUI — open `.tailspin` files directly |
| **ktrace** | `ktrace trace -R file.tailspin` |
| **spindump** | `spindump -file file.tailspin` |
| **fs_usage** | File system activity analysis |

## Why tailspin matters for pause-monitor

**The problem:** When a system pause occurs, our daemon is frozen too. We cannot observe the pause while it's happening. Any metrics we collect are from *before* and *after* the pause, not *during*.

**The solution:** Tailspin runs in the kernel, continuously recording to a rolling buffer. When we detect a pause (after it ends), we can save the buffer and see what the kernel was doing *during* the freeze.

**What kdebug events reveal:**
- Scheduler events — was a thread starved?
- Disk I/O — was the system waiting on storage?
- Interrupts — hardware issues?
- Mach messages — IPC deadlock?
- File system ops — journaling stall?

This is the only way to see inside a system freeze.

## Integration with pause-monitor

**Status:** Tailspin is the primary forensics capture tool. Live spindump is not used.

**Flow:**
1. `sudo -n tailspin save -o <path>` — capture kernel buffer (privileged)
2. `spindump -i <file> -stdout` — decode to readable format (unprivileged)
3. Parse and store in database

**Sudoers rule** (`/etc/sudoers.d/pause-monitor`):
```bash
<user> ALL = (root) NOPASSWD: /usr/bin/tailspin save -o /Users/<user>/.local/share/pause-monitor/events/*
```

**Why tailspin over live spindump:** During a system pause, our daemon is frozen too. We can only detect the pause after it ends. Live spindump shows post-recovery state; tailspin's buffer shows what happened during the freeze.

The ~20 second capture window means tailspin should be enabled persistently so data is available when pauses occur.

## Related Tools

- **ktrace(1)** — Lower-level kernel tracing
- **spindump(8)** — Stack sampling for hangs
- **fs_usage(1)** — File system monitoring
- **powermetrics(1)** — CPU/GPU power metrics

## Source

- `man tailspin` (macOS)
- Verified on macOS 15 (Sequoia)
