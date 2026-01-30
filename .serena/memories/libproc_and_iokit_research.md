# macOS Process Monitoring APIs Research

**Date:** 2026-01-29
**Purpose:** Document native macOS APIs for process monitoring without elevated privileges

## Executive Summary

We don't need to shell out to `top` or any command-line tool. macOS provides direct APIs via `libproc.dylib` and IOKit that give us MORE data with LESS overhead, all without sudo.

**Key insight:** Activity Monitor, htop, btop, mactop all use these same APIs internally.

---

## libproc.dylib

**Location:** `/usr/lib/libproc.dylib`
**Header:** `/usr/include/libproc.h` (ships with Xcode Command Line Tools)
**Available since:** macOS 10.5 (2007)

### Core Functions

| Function | Purpose |
|----------|---------|
| `proc_pid_rusage(pid, flavor, buffer)` | Resource usage (richest single call) |
| `proc_pidinfo(pid, flavor, arg, buffer, size)` | Process/task information |
| `proc_listallpids(buffer, size)` | List all PIDs |
| `proc_listpids(type, typeinfo, buffer, size)` | List PIDs by criteria |
| `proc_pidpath(pid, buffer, size)` | Executable path |
| `proc_name(pid, buffer, size)` | Process name |

### proc_pid_rusage — RUSAGE_INFO_V4 Structure

The richest single API call. Returns:

```
IDENTIFICATION
├── ri_uuid[16]                      — Process UUID

CPU TIME
├── ri_user_time                     — User CPU time
├── ri_system_time                   — System CPU time
├── ri_child_user_time               — Children's user time
├── ri_child_system_time             — Children's system time
├── ri_billed_system_time            — Billed system time
├── ri_serviced_system_time          — Serviced system time
└── ri_runnable_time                 — Time spent runnable

CPU TIME BY QoS CLASS
├── ri_cpu_time_qos_default
├── ri_cpu_time_qos_maintenance
├── ri_cpu_time_qos_background
├── ri_cpu_time_qos_utility
├── ri_cpu_time_qos_legacy
├── ri_cpu_time_qos_user_initiated
└── ri_cpu_time_qos_user_interactive

MEMORY
├── ri_resident_size                 — Resident memory ("Real Memory" in Activity Monitor)
├── ri_phys_footprint                — Physical footprint ("Memory" in Activity Monitor)
├── ri_wired_size                    — Wired/locked memory
├── ri_pageins                       — Page-in count
├── ri_child_pageins                 — Children's page-ins
├── ri_lifetime_max_phys_footprint   — Peak memory ever
└── ri_interval_max_phys_footprint   — Peak memory this interval

DISK I/O
├── ri_diskio_bytesread              — Bytes read from disk
├── ri_diskio_byteswritten           — Bytes written to disk
└── ri_logical_writes                — Logical write operations

POWER/WAKEUPS
├── ri_pkg_idle_wkups                — Package idle wakeups
├── ri_interrupt_wkups               — Interrupt wakeups
├── ri_child_pkg_idle_wkups          — Children's package wakeups
└── ri_child_interrupt_wkups         — Children's interrupt wakeups

CPU PERFORMANCE COUNTERS
├── ri_instructions                  — CPU instructions executed
└── ri_cycles                        — CPU cycles consumed

ENERGY
├── ri_billed_energy                 — Energy billed to this process
└── ri_serviced_energy               — Energy serviced

TIMING
├── ri_proc_start_abstime            — Process start time
├── ri_proc_exit_abstime             — Process exit time
└── ri_child_elapsed_abstime         — Children's elapsed time
```

### proc_pidinfo — PROC_PIDTASKINFO Structure

```
THREADS
├── pti_threadnum                    — Number of threads
└── pti_numrunning                   — Running threads

MEMORY
├── pti_virtual_size                 — Virtual memory size
├── pti_resident_size                — Resident memory size

CPU TIME
├── pti_total_user                   — Total user time
├── pti_total_system                 — Total system time
├── pti_threads_user                 — Threads user time
└── pti_threads_system               — Threads system time

PAGE FAULTS
├── pti_faults                       — Page faults
├── pti_pageins                      — Page-ins
└── pti_cow_faults                   — Copy-on-write faults

MACH IPC
├── pti_messages_sent                — Mach messages sent
└── pti_messages_received            — Mach messages received

SYSCALLS
├── pti_syscalls_mach                — Mach syscall count
└── pti_syscalls_unix                — BSD/Unix syscall count

SCHEDULING
├── pti_csw                          — Context switches
└── pti_priority                     — Task priority
```

### proc_pidinfo — PROC_PIDTBSDINFO Structure

```
├── pbi_pid                          — Process ID
├── pbi_ppid                         — Parent PID
├── pbi_status                       — Process status (SIDL, SRUN, SSLEEP, SSTOP, SZOMB)
├── pbi_flags                        — Process flags
├── pbi_nice                         — Nice value
├── pbi_comm[MAXCOMLEN]              — Command name (16 chars)
└── pbi_start_tvsec/tvusec           — Process start time
```

### Permissions

- **Same-user processes:** Full access, no sudo required
- **Other users' processes:** Blocked (EPERM)
- **Root (UID 0):** Full access to all processes

---

## IOKit / IOReport — GPU and Power Metrics

### Per-Process GPU Time (No Sudo)

Available via `AGXDeviceUserClient` objects in IORegistry:

```
IORegistry
└── AGXAccelerator
    └── AGXDeviceUserClient (one per GPU-using process)
        ├── IOUserClientCreator = "pid 682, WindowServer"
        └── AppUsage (array)
            └── accumulatedGPUTime = nanoseconds
```

**Quick test:**
```bash
ioreg -r -c AGXDeviceUserClient | grep -E "(IOUserClientCreator|accumulatedGPUTime)"
```

### System-Wide GPU Metrics via IOReport (No Sudo)

| Metric | IOReport Channel | Notes |
|--------|------------------|-------|
| GPU utilization % | "GPU Stats" | active_residency / total_residency |
| GPU power (watts) | "Energy Model" → "GPU Energy" | Direct reading |
| GPU frequency | "GPU Stats" | Weighted average from P-states |

**GPU utilization calculation:**
```c
uint64_t idle = IOReportStateGetResidency(channel, 0);  // State 0 = idle
uint64_t active = sum_of_other_states;
float utilization = (float)active / (active + idle) * 100;
```

### Per-Process GPU Percentage Calculation

```python
# 1. Get accumulatedGPUTime delta for each process (from AGXDeviceUserClient)
# 2. Get system-wide GPU % (from IOReport)
# 3. Scale per-process times to match system total

per_process_gpu_percent = (process_delta_ms / total_all_processes_delta_ms) * system_gpu_percent
```

### Other IOReport Channels (No Sudo)

- "CPU Stats" — Per-cluster CPU utilization
- "Energy Model" — CPU, GPU, ANE, DRAM power
- GPU DVFS frequencies — via `voltage-states9` in pmgr device

### Temperature Sensors

- SMC via `AppleSMCKeysEndpoint`
- IOHIDEventSystemClient (private API)

---

## Python Integration via ctypes

### Basic Setup

```python
import ctypes

libproc = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)

RUSAGE_INFO_V4 = 4

class RusageInfoV4(ctypes.Structure):
    _fields_ = [
        ("ri_uuid", ctypes.c_uint8 * 16),
        ("ri_user_time", ctypes.c_uint64),
        ("ri_system_time", ctypes.c_uint64),
        ("ri_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_interrupt_wkups", ctypes.c_uint64),
        ("ri_pageins", ctypes.c_uint64),
        ("ri_wired_size", ctypes.c_uint64),
        ("ri_resident_size", ctypes.c_uint64),
        ("ri_phys_footprint", ctypes.c_uint64),
        # ... (see full structure above)
    ]

libproc.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
libproc.proc_pid_rusage.restype = ctypes.c_int

def get_rusage(pid):
    rusage = RusageInfoV4()
    result = libproc.proc_pid_rusage(pid, RUSAGE_INFO_V4, ctypes.byref(rusage))
    return rusage if result == 0 else None
```

---

## Comparison: Current vs Native API

| Metric | Current (top) | Native API | Improvement |
|--------|---------------|------------|-------------|
| CPU % | ✓ | ✓ (calculate from time deltas) | No subprocess |
| Memory | ✓ | ✓ (more detail) | resident, footprint, wired |
| State | ✓ | ✓ | Same |
| Page-ins | ✓ | ✓ | Same |
| Context switches | ✓ | ✓ | Same |
| Syscalls | ✓ | ✓ (Mach + BSD separate) | More detail |
| Threads | ✓ | ✓ | Same |
| **Disk I/O** | ✗ | ✓ | NEW |
| **Instructions** | ✗ | ✓ | NEW |
| **Cycles** | ✗ | ✓ | NEW |
| **Energy** | ✗ | ✓ | NEW |
| **Wakeups** | ✗ | ✓ | NEW |
| **QoS breakdown** | ✗ | ✓ | NEW |
| **Peak memory** | ✗ | ✓ | NEW |
| **GPU time** | ✗ | ✓ (via IOKit) | NEW |
| **GPU %** | ✗ | ✓ (via IOReport) | NEW |

---

## Tools That Use These APIs

| Tool | APIs Used |
|------|-----------|
| Activity Monitor | proc_pidinfo, proc_pid_rusage via sysmond |
| top | proc_pidinfo, sysctl |
| htop | proc_pidinfo, proc_pid_rusage, sysctl, task_for_pid |
| btop | proc_pidinfo, proc_pid_rusage, sysctl, IOKit |
| mactop | proc_pidinfo, IOReport, AGXDeviceUserClient |
| macmon | IOReport, SMC, IOHIDEventSystemClient |

---

## Apple Silicon Notes

1. **Timing units changed:** `ri_user_time`/`ri_system_time` are NOT nanoseconds on Apple Silicon — approximately 40ns units
2. **Use mach_timebase_info()** for conversion
3. **GPU metrics** available via AGXDeviceUserClient and IOReport

---

## Sources

- [Apple darwin-xnu proc_info.h](https://github.com/apple/darwin-xnu/blob/main/bsd/sys/proc_info.h)
- [Apple darwin-xnu resource.h](https://github.com/apple/darwin-xnu/blob/main/bsd/sys/resource.h)
- [Activity Monitor Anatomy](https://www.bazhenov.me/posts/activity-monitor-anatomy/)
- [macmon GitHub](https://github.com/vladkens/macmon)
- [mactop GitHub](https://github.com/metaspartan/mactop)
- [socpowerbud GitHub](https://github.com/dehydratedpotato/socpowerbud)
- [btop GitHub](https://github.com/aristocratos/btop)
- [psutil macOS source](https://github.com/giampaolo/psutil/blob/master/psutil/_psutil_osx.c)
- [How to get macOS power metrics with Rust](https://medium.com/@vladkens/how-to-get-macos-power-metrics-with-rust-d42b0ad53967)
