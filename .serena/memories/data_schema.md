# Unified Data Schema

> **Philosophy: One schema for everything.**
>
> ProcessScore is THE canonical data representation used across the entire application. Collector creates it, ring buffer stores it, socket broadcasts it, TUI displays it, storage persists it. 
>
> **DO NOT create alternative representations.** If you need process data anywhere in the application, use ProcessScore. No subsetting, no reshaping, no "simplified versions." One schema, everywhere.

**Last updated:** 2026-01-29

---

## Core Types

```python
@dataclass
class MetricValue:
    """A metric with current value and buffer-window range."""
    current: float | int
    low: float | int
    high: float | int

@dataclass  
class MetricValueStr:
    """A categorical metric with hierarchy (for state/band)."""
    current: str
    low: str   # best (least concerning)
    high: str  # worst (most concerning)
```

---

## ProcessScore — The Canonical Schema

```python
@dataclass
class ProcessScore:
    """Single process with metrics and buffer-window ranges.
    
    This is THE canonical data schema. DO NOT create alternatives.
    """
    
    # ─────────────────────────────────────────────────────────────
    # Identity (no range — these don't vary)
    # ─────────────────────────────────────────────────────────────
    pid: int                    # Process ID
    command: str                # Process name
    captured_at: float          # Unix timestamp of this sample
    
    # ─────────────────────────────────────────────────────────────
    # CPU
    # ─────────────────────────────────────────────────────────────
    cpu: MetricValue            # CPU % (calculated from time delta)
    
    # ─────────────────────────────────────────────────────────────
    # Memory
    # ─────────────────────────────────────────────────────────────
    mem: MetricValue            # Physical footprint (bytes)
    mem_peak: int               # Lifetime peak (ri_lifetime_max_phys_footprint)
    pageins: MetricValue        # Page-in count
    faults: MetricValue         # Page faults (pti_faults)
    
    # ─────────────────────────────────────────────────────────────
    # Disk I/O
    # ─────────────────────────────────────────────────────────────
    disk_io: MetricValue        # Cumulative bytes (read + write)
    disk_io_rate: MetricValue   # Bytes/sec (calculated from delta)
    
    # ─────────────────────────────────────────────────────────────
    # Activity
    # ─────────────────────────────────────────────────────────────
    csw: MetricValue            # Context switches
    syscalls: MetricValue       # Mach + Unix syscalls combined
    threads: MetricValue        # Thread count
    mach_msgs: MetricValue      # Mach messages (sent + received)
    
    # ─────────────────────────────────────────────────────────────
    # Efficiency
    # ─────────────────────────────────────────────────────────────
    instructions: MetricValue   # CPU instructions executed
    cycles: MetricValue         # CPU cycles consumed
    ipc: MetricValue            # Instructions per cycle (calculated)
    
    # ─────────────────────────────────────────────────────────────
    # Power
    # ─────────────────────────────────────────────────────────────
    energy: MetricValue         # Energy billed (ri_billed_energy)
    energy_rate: MetricValue    # Energy/sec (calculated from delta)
    wakeups: MetricValue        # Idle wakeups (pkg + interrupt)
    
    # ─────────────────────────────────────────────────────────────
    # State (categorical with hierarchy)
    # ─────────────────────────────────────────────────────────────
    state: MetricValueStr       # Process state (idle/sleeping/running/stuck/etc)
    priority: MetricValue       # Task priority (pti_priority)
    
    # ─────────────────────────────────────────────────────────────
    # Scoring (our assessment)
    # ─────────────────────────────────────────────────────────────
    score: MetricValue          # Stress score 0-100
    band: MetricValueStr        # low/medium/elevated/high/critical
    categories: list[str]       # Why selected as rogue
```

---

## Field Summary

| Category | Fields | Type |
|----------|--------|------|
| **Identity** | pid, command, captured_at | Plain values |
| **CPU** | cpu | MetricValue |
| **Memory** | mem, mem_peak, pageins, faults | 3 MetricValue + 1 int |
| **Disk I/O** | disk_io, disk_io_rate | 2 MetricValue |
| **Activity** | csw, syscalls, threads, mach_msgs | 4 MetricValue |
| **Efficiency** | instructions, cycles, ipc | 3 MetricValue |
| **Power** | energy, energy_rate, wakeups | 3 MetricValue |
| **State** | state, priority | MetricValueStr + MetricValue |
| **Scoring** | score, band, categories | MetricValue + MetricValueStr + list |

---

## Data Sources

| Field | Source | Notes |
|-------|--------|-------|
| cpu | Calculated | Delta of ri_user_time + ri_system_time |
| mem | ri_phys_footprint | "Memory" in Activity Monitor |
| mem_peak | ri_lifetime_max_phys_footprint | Lifetime high water mark |
| pageins | ri_pageins | |
| faults | pti_faults | |
| disk_io | ri_diskio_bytesread + ri_diskio_byteswritten | Combined |
| disk_io_rate | Calculated | Delta of disk_io |
| csw | pti_csw | |
| syscalls | pti_syscalls_mach + pti_syscalls_unix | Combined |
| threads | pti_threadnum | |
| mach_msgs | pti_messages_sent + pti_messages_received | Combined |
| instructions | ri_instructions | |
| cycles | ri_cycles | |
| ipc | Calculated | instructions / cycles |
| energy | ri_billed_energy | |
| energy_rate | Calculated | Delta of energy |
| wakeups | ri_pkg_idle_wkups + ri_interrupt_wkups | Combined |
| state | pbi_status | Mapped to string |
| priority | pti_priority | |
| score | Calculated | Weighted scoring formula |
| band | Derived | From score via band thresholds |
| categories | Selection logic | Why process was selected as rogue |

---

## Calculated Fields

These require delta computation (current - previous) / time_delta:
- `cpu` — CPU time delta → percentage
- `disk_io_rate` — Disk bytes delta → bytes/sec
- `energy_rate` — Energy delta → energy/sec
- `ipc` — instructions / cycles (no delta needed, just division)

---

## Low/High Computation

For each MetricValue field:
1. Get current value from this sample
2. Scan ring buffer for same PID
3. Find min across all samples → `low`
4. Find max across all samples → `high`

For MetricValueStr (state, band):
- Use severity hierarchy to determine "low" (best) and "high" (worst)

---

## Hierarchies

### State Severity
```python
STATE_SEVERITY = {
    "idle": 0,
    "sleeping": 1,
    "running": 2,
    "stopped": 3,
    "halted": 4,
    "zombie": 5,
    "stuck": 6,
}
```

### Band Severity
```python
BAND_SEVERITY = {
    "low": 0,
    "medium": 1,
    "elevated": 2,
    "high": 3,
    "critical": 4,
}
```

---

## Serialization

```python
def to_dict(self) -> dict:
    """Serialize for socket/storage."""

@classmethod
def from_dict(cls, data: dict) -> "ProcessScore":
    """Deserialize from socket/storage."""
```

---

## JSON Wire Format Example

```json
{
  "pid": 1234,
  "command": "Safari",
  "captured_at": 1706540000.123,
  
  "cpu": {"current": 45.2, "low": 12.0, "high": 80.5},
  
  "mem": {"current": 1073741824, "low": 536870912, "high": 2147483648},
  "mem_peak": 3221225472,
  "pageins": {"current": 150, "low": 50, "high": 300},
  "faults": {"current": 1000, "low": 200, "high": 5000},
  
  "disk_io": {"current": 52428800, "low": 10485760, "high": 104857600},
  "disk_io_rate": {"current": 1048576.0, "low": 0.0, "high": 5242880.0},
  
  "csw": {"current": 500, "low": 100, "high": 2000},
  "syscalls": {"current": 10000, "low": 1000, "high": 50000},
  "threads": {"current": 12, "low": 8, "high": 15},
  "mach_msgs": {"current": 200, "low": 50, "high": 1000},
  
  "instructions": {"current": 500000000, "low": 100000000, "high": 900000000},
  "cycles": {"current": 250000000, "low": 80000000, "high": 400000000},
  "ipc": {"current": 2.0, "low": 1.2, "high": 2.5},
  
  "energy": {"current": 1000000, "low": 100000, "high": 5000000},
  "energy_rate": {"current": 50000.0, "low": 5000.0, "high": 200000.0},
  "wakeups": {"current": 100, "low": 10, "high": 500},
  
  "state": {"current": "running", "low": "idle", "high": "stuck"},
  "priority": {"current": 31, "low": 20, "high": 47},
  
  "score": {"current": 65, "low": 30, "high": 85},
  "band": {"current": "elevated", "low": "low", "high": "high"},
  "categories": ["cpu", "mem", "pageins"]
}
```

---

## Dropped Fields

| Field | Reason |
|-------|--------|
| cmprs | Not available from libproc, was always 0 |
| sysbsd | Renamed to `syscalls`, now includes mach + unix |

---

## Future: GPU (requires IOKit)

When implemented:
```python
gpu_time: MetricValue       # accumulatedGPUTime from AGXDeviceUserClient
gpu_rate: MetricValue       # GPU time/sec (calculated)
```
