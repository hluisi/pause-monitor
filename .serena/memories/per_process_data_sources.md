# Per-Process Data Sources on macOS

Research completed 2026-01-23. This documents all available methods for getting per-process metrics on macOS Apple Silicon.

## Summary

| Source | Sudo? | CPU | Memory | Disk I/O | Pageins | Wakeups | GPU | Energy |
|--------|-------|-----|--------|----------|---------|---------|-----|--------|
| `top` | No | ✓ % | ✓ bytes | ✗ | ✓ | ✓ (idlew) | indirect | ✓ (POWER) |
| psutil | No | ✓ % | ✓ rss/vms | ✗ | ✓ | ✗ | ✗ | ✗ |
| proc_pid_rusage | No* | ✗ | ✓ | ✓ bytes | ✓ | ✓ | ✗ | ✗ |
| powermetrics | Yes | ✓ ms/s | ✗ | ✓ bytes/s | ✓ /s | ✓ /s | ✗ plist | ✓ |

*proc_pid_rusage blocked for some system processes

## `top` Command (No Sudo)

Best for real-time sorted process list.

```bash
top -l 2 -s 1 -stats pid,command,cpu,power,pageins,faults,idlew -n 15 -o power
```

Available stats: pid, command, cpu, cpu_me, cpu_others, csw, time, threads, ports, mregion, mem, rprvt, purg, vsize, vprvt, kprvt, kshrd, pgrp, ppid, state, uid, wq, faults, cow, user, msgsent, msgrecv, sysbsd, sysmach, pageins, boosts, instrs, cycles, idlew, power

**POWER column = Activity Monitor's Energy Impact** - A composite metric including:
- CPU usage
- GPU usage (indirect)
- Disk I/O
- Network activity
- Wakeup frequency
- Machine-specific weighting from `/usr/share/pmenergy/Mac-<id>.plist`

## proc_pid_rusage() via ctypes (No Sudo)

Direct access to per-process resource usage. Works for user processes; blocked for some system processes.

```python
import ctypes
from ctypes import c_int, c_uint64, Structure, POINTER, byref

libproc = ctypes.CDLL('/usr/lib/libproc.dylib')

class rusage_info_v4(Structure):
    _fields_ = [
        ('ri_uuid', ctypes.c_uint8 * 16),
        ('ri_user_time', c_uint64),
        ('ri_system_time', c_uint64),
        ('ri_pkg_idle_wkups', c_uint64),
        ('ri_interrupt_wkups', c_uint64),
        ('ri_pageins', c_uint64),
        ('ri_wired_size', c_uint64),
        ('ri_resident_size', c_uint64),
        ('ri_phys_footprint', c_uint64),
        ('ri_proc_start_abstime', c_uint64),
        ('ri_proc_exit_abstime', c_uint64),
        ('ri_child_user_time', c_uint64),
        ('ri_child_system_time', c_uint64),
        ('ri_child_pkg_idle_wkups', c_uint64),
        ('ri_child_interrupt_wkups', c_uint64),
        ('ri_child_pageins', c_uint64),
        ('ri_child_elapsed_abstime', c_uint64),
        ('ri_diskio_bytesread', c_uint64),
        ('ri_diskio_byteswritten', c_uint64),
    ]

RUSAGE_INFO_V4 = 4
libproc.proc_pid_rusage.argtypes = [c_int, c_int, POINTER(rusage_info_v4)]
libproc.proc_pid_rusage.restype = c_int
```

## powermetrics (Sudo Required)

Most comprehensive but requires root. Current daemon already uses this.

Key flags discovered:
- `--show-process-energy` - adds energy_impact and energy_impact_per_s to plist
- `--show-process-gpu` - adds GPU ms/s to human-readable output only (NOT plist!)
- `--show-process-io` - adds disk I/O
- `--show-process-netstats` - adds network stats

**Limitation:** GPU metrics only appear in human-readable format, not plist format.

## Recommended Approach for Per-Process Stress

**Option A: Use `top` POWER as primary metric**
- Pros: Apple's own composite, includes GPU indirectly, no sudo
- Cons: Less diagnostic granularity

**Option B: Hybrid approach**
- Use powermetrics for system-wide + detailed per-process when running as root
- Fall back to `top` + psutil when running without sudo
- Provides graceful degradation

**Option C: Always use powermetrics (current approach)**
- Daemon requires sudo anyway for full system metrics
- Most accurate per-process data
- Add --show-process-energy for energy_impact_per_s

## Sources

- https://blog.mozilla.org/nnethercote/2015/08/26/what-does-the-os-x-activity-monitors-energy-impact-actually-measure/
- https://github.com/vladkens/macmon
- https://github.com/metaspartan/mactop
- https://ariya.io/2012/07/mac-os-x-tracking-disk-io-activities
