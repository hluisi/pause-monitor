---
id: schema-data-model
type: schema
domain: project
subject: data-model
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: [data_schema]
tags: []
related: []
sources: []
---

# Unified Data Schema

> **Philosophy: One schema for everything.**
>
> ProcessScore is THE canonical data representation used across the entire **Rogue Hunter** application. Collector creates it, ring buffer stores it, socket broadcasts it, TUI displays it, storage persists it. 
>
> **DO NOT create alternative representations.** If you need process data anywhere in the application, use ProcessScore. No subsetting, no reshaping, no "simplified versions." One schema, everywhere.

**Last updated:** 2026-02-02
**Schema version:** 18

## Rogue Hunter Scoring

ProcessScore uses a **disproportionate-share** scoring system that identifies processes consuming disproportionate system resources:

- **CPU share**: Percentage of total system CPU this process uses
- **GPU share**: Percentage of total system GPU this process uses  
- **Memory share**: Percentage of total system memory this process uses
- **Disk share**: Percentage of total system disk I/O this process uses
- **Wakeups share**: Percentage of total system wakeups this process causes

The **disproportionality** is the highest share value, and **dominant_resource** identifies which resource the process dominates.

Score is calculated using a logarithmic curve based on multiples of fair share:
- 1.0x fair share = score 0 (using exactly your fair share)
- 50x fair share = score ~56 (high band)
- 100x fair share = score ~66 (high band)
- 200x fair share = score ~76 (critical band)

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
    pageins: MetricValue        # Page-in count (cumulative)
    pageins_rate: MetricValue   # Page-ins per second (calculated)
    faults: MetricValue         # Page faults (cumulative)
    faults_rate: MetricValue    # Faults per second (calculated)
    
    # ─────────────────────────────────────────────────────────────
    # Disk I/O
    # ─────────────────────────────────────────────────────────────
    disk_io: MetricValue        # Cumulative bytes (read + write)
    disk_io_rate: MetricValue   # Bytes/sec (calculated from delta)
    
    # ─────────────────────────────────────────────────────────────
    # Activity
    # ─────────────────────────────────────────────────────────────
    csw: MetricValue            # Context switches (cumulative)
    csw_rate: MetricValue       # Context switches per second (calculated)
    syscalls: MetricValue       # Mach + Unix syscalls combined (cumulative)
    syscalls_rate: MetricValue  # Syscalls per second (calculated)
    threads: MetricValue        # Thread count (instantaneous)
    mach_msgs: MetricValue      # Mach messages (cumulative)
    mach_msgs_rate: MetricValue # Mach messages per second (calculated)
    
    # ─────────────────────────────────────────────────────────────
    # Efficiency
    # ─────────────────────────────────────────────────────────────
    instructions: MetricValue   # CPU instructions executed
    cycles: MetricValue         # CPU cycles consumed
    ipc: MetricValue            # Instructions per cycle (calculated)
    
    # ─────────────────────────────────────────────────────────────
    # Power
    # ─────────────────────────────────────────────────────────────
    energy: MetricValue         # Energy billed (cumulative)
    energy_rate: MetricValue    # Energy/sec (calculated from delta)
    wakeups: MetricValue        # Idle wakeups (cumulative)
    wakeups_rate: MetricValue   # Wakeups per second (calculated)
    
    # ─────────────────────────────────────────────────────────────
    # Contention (NEW)
    # ─────────────────────────────────────────────────────────────
    runnable_time: MetricValue       # Cumulative runnable time (ns)
    runnable_time_rate: MetricValue  # ms runnable per second (calculated)
    qos_interactive: MetricValue     # Cumulative QoS interactive time (ns)
    qos_interactive_rate: MetricValue # ms interactive QoS per second (calculated)
    
    # ─────────────────────────────────────────────────────────────
    # State (categorical with hierarchy)
    # ─────────────────────────────────────────────────────────────
    state: MetricValueStr       # Process state (idle/sleeping/running/stuck/etc)
    priority: MetricValue       # Task priority (pti_priority)
    
    # ─────────────────────────────────────────────────────────────
    # Scoring (disproportionate-share system)
    # ─────────────────────────────────────────────────────────────
    score: int                      # Final score 0-100 from disproportionality
    band: str                       # low/medium/elevated/high/critical
    cpu_share: float                # Multiple of fair CPU share (1.0 = fair share, 10.0 = 10x fair share)
    gpu_share: float                # Multiple of fair GPU share (can be well above 1.0)
    mem_share: float                # Multiple of fair memory share (can be well above 1.0)
    disk_share: float               # Multiple of fair disk I/O share (can be well above 1.0)
    wakeups_share: float            # Multiple of fair wakeups share (can be well above 1.0)
    disproportionality: float       # Highest share value (max of above)
    dominant_resource: DominantResource  # cpu/gpu/mem/disk/wakeups/none
```

---

## Field Summary

| Category | Fields | Type |
|----------|--------|------|
| **Identity** | pid, command, captured_at | Plain values |
| **CPU** | cpu | MetricValue |
| **Memory** | mem, mem_peak, pageins, pageins_rate, faults, faults_rate | 5 MetricValue + 1 int |
| **Disk I/O** | disk_io, disk_io_rate | 2 MetricValue |
| **Activity** | csw, csw_rate, syscalls, syscalls_rate, threads, mach_msgs, mach_msgs_rate | 7 MetricValue |
| **Efficiency** | instructions, cycles, ipc | 3 MetricValue |
| **Power** | energy, energy_rate, wakeups, wakeups_rate | 4 MetricValue |
| **Contention** | runnable_time, runnable_time_rate, qos_interactive, qos_interactive_rate | 4 MetricValue |
| **State** | state, priority | MetricValueStr + MetricValue |
| **Scoring** | score, band, cpu_share, gpu_share, mem_share, disk_share, wakeups_share, disproportionality, dominant_resource | int + str + 6 float + DominantResource |

---

## Data Sources

| Field | Source | Notes |
|-------|--------|-------|
| cpu | Calculated | Delta of ri_user_time + ri_system_time |
| mem | ri_phys_footprint | "Memory" in Activity Monitor |
| mem_peak | ri_lifetime_max_phys_footprint | Lifetime high water mark |
| pageins | ri_pageins | Cumulative page-ins |
| pageins_rate | Calculated | Delta of pageins / time |
| faults | pti_faults | Cumulative faults |
| faults_rate | Calculated | Delta of faults / time |
| disk_io | ri_diskio_bytesread + ri_diskio_byteswritten | Combined |
| disk_io_rate | Calculated | Delta of disk_io |
| csw | pti_csw | Cumulative context switches |
| csw_rate | Calculated | Delta of csw / time |
| syscalls | pti_syscalls_mach + pti_syscalls_unix | Combined cumulative |
| syscalls_rate | Calculated | Delta of syscalls / time |
| threads | pti_threadnum | Instantaneous count |
| mach_msgs | pti_messages_sent + pti_messages_received | Combined cumulative |
| mach_msgs_rate | Calculated | Delta of mach_msgs / time |
| instructions | ri_instructions | Cumulative |
| cycles | ri_cycles | Cumulative |
| ipc | Calculated | instructions / cycles |
| energy | ri_billed_energy | Cumulative |
| energy_rate | Calculated | Delta of energy |
| wakeups | ri_pkg_idle_wkups + ri_interrupt_wkups | Combined cumulative |
| wakeups_rate | Calculated | Delta of wakeups / time |
| runnable_time | ri_runnable_time | Cumulative runnable time (ns) |
| runnable_time_rate | Calculated | Delta → ms runnable per second |
| qos_interactive | ri_cpu_time_qos_user_interactive | Cumulative QoS interactive time (ns) |
| qos_interactive_rate | Calculated | Delta → ms interactive per second |
| state | pbi_status | Mapped to string |
| priority | pti_priority | |
| score | Calculated | From disproportionality via graduated thresholds |
| band | Derived | From score via band thresholds |
| cpu_share | Calculated | cpu / (active_processes * fair_share) |
| gpu_share | Calculated | gpu_time_rate / system_gpu_total |
| mem_share | Calculated | mem / system_mem_total |
| disk_share | Calculated | disk_io_rate / system_disk_total |
| wakeups_share | Calculated | wakeups_rate / system_wakeups_total |
| disproportionality | Derived | max(cpu_share, gpu_share, mem_share, disk_share, wakeups_share) |
| dominant_resource | Derived | Which share is highest |

---

## Calculated Fields

### Rate Calculations (require delta computation)

All rate fields are computed as `(current - previous) / time_delta`:

| Field | Calculation | Unit |
|-------|-------------|------|
| cpu | CPU time delta | percentage |
| disk_io_rate | Disk bytes delta | bytes/sec |
| energy_rate | Energy delta | energy/sec |
| pageins_rate | Pageins delta | page-ins/sec |
| faults_rate | Faults delta | faults/sec |
| csw_rate | Context switch delta | switches/sec |
| syscalls_rate | Syscalls delta | syscalls/sec |
| mach_msgs_rate | Mach messages delta | msgs/sec |
| wakeups_rate | Wakeups delta | wakeups/sec |
| runnable_time_rate | Runnable time delta → ms | ms runnable/sec |
| qos_interactive_rate | QoS interactive delta → ms | ms interactive/sec |

### Derived Calculations (no delta needed)

| Field | Calculation |
|-------|-------------|
| ipc | instructions / cycles |
| cpu_share | cpu / (active_processes * 100 / core_count) |
| gpu_share | gpu_time_rate / system_gpu_total |
| mem_share | mem / system_mem_total |
| disk_share | disk_io_rate / system_disk_total |
| wakeups_share | wakeups_rate / system_wakeups_total |
| disproportionality | max(cpu_share, gpu_share, mem_share, disk_share, wakeups_share) |
| dominant_resource | argmax of shares (cpu/gpu/mem/disk/wakeups) |
| score | Graduated from disproportionality |
| band | Score → threshold mapping |

### Scoring Algorithm (Disproportionate-Share System)

Each resource share is calculated as a multiple of fair share:
- **CPU share**: Process CPU / (100% / active_processes) - how many times fair share
- **GPU share**: Process GPU rate / (system GPU total / active_processes)
- **Memory share**: Process memory / (system memory / active_processes)
- **Disk share**: Process disk I/O rate / (system disk total / active_processes)
- **Wakeups share**: Process wakeups rate / (system wakeups / active_processes)

Values are multiples: 1.0 = exactly fair share, 10.0 = using 10x your fair share.

**Logarithmic Score Calculation:**
```python
# Weight each resource share by its ResourceWeight
weighted = {
    "cpu": cpu_share * weights.cpu,
    "gpu": gpu_share * weights.gpu,
    "memory": mem_share * weights.memory,
    "disk": disk_share * weights.disk_io,
    "wakeups": wakeups_share * weights.wakeups,
}
total_weighted = sum(weighted.values())

# Logarithmic curve: log2(weighted) * 10
# At fair share (1.0): score = 0
# At 50x fair share: score ≈ 56 (high band)
# At 100x fair share: score ≈ 66 (high band)
# At 200x fair share: score ≈ 76 (critical band)
if total_weighted <= 1.0:
    score = 0
else:
    score = log2(total_weighted) * 10
```

**State Multipliers (post-score):**
Processes in non-running states get discounted scores since they're not actively causing issues.

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
  "pageins_rate": {"current": 12.5, "low": 0.0, "high": 45.0},
  "faults": {"current": 1000, "low": 200, "high": 5000},
  "faults_rate": {"current": 250.0, "low": 0.0, "high": 1200.0},
  
  "disk_io": {"current": 52428800, "low": 10485760, "high": 104857600},
  "disk_io_rate": {"current": 1048576.0, "low": 0.0, "high": 5242880.0},
  
  "csw": {"current": 2688485, "low": 2680000, "high": 2688485},
  "csw_rate": {"current": 1250.0, "low": 500.0, "high": 3200.0},
  "syscalls": {"current": 10000000, "low": 9900000, "high": 10000000},
  "syscalls_rate": {"current": 8500.0, "low": 2000.0, "high": 15000.0},
  "threads": {"current": 12, "low": 8, "high": 15},
  "mach_msgs": {"current": 50000, "low": 49000, "high": 50000},
  "mach_msgs_rate": {"current": 450.0, "low": 100.0, "high": 800.0},
  
  "instructions": {"current": 500000000, "low": 100000000, "high": 900000000},
  "cycles": {"current": 250000000, "low": 80000000, "high": 400000000},
  "ipc": {"current": 2.0, "low": 1.2, "high": 2.5},
  
  "energy": {"current": 1000000, "low": 100000, "high": 5000000},
  "energy_rate": {"current": 50000.0, "low": 5000.0, "high": 200000.0},
  "wakeups": {"current": 5000, "low": 4800, "high": 5000},
  "wakeups_rate": {"current": 85.0, "low": 20.0, "high": 150.0},
  
  "runnable_time": {"current": 5000000000, "low": 4000000000, "high": 5000000000},
  "runnable_time_rate": {"current": 45.0, "low": 10.0, "high": 80.0},
  "qos_interactive": {"current": 2000000000, "low": 1500000000, "high": 2000000000},
  "qos_interactive_rate": {"current": 25.0, "low": 5.0, "high": 40.0},
  
  "state": {"current": "running", "low": "idle", "high": "running"},
  "priority": {"current": 31, "low": 20, "high": 47},
  
  "score": 65,
  "band": "elevated",
  "cpu_share": 3.5,
  "gpu_share": 0.0,
  "mem_share": 1.2,
  "disk_share": 2.8,
  "wakeups_share": 1.5,
  "disproportionality": 3.5,
  "dominant_resource": "cpu"
}
```

---

## Dropped Fields

| Field | Reason |
|-------|--------|
| cmprs | Not available from libproc, was always 0 |
| sysbsd | Renamed to `syscalls`, now includes mach + unix |
| categories | Replaced by `dominant_resource` + `disproportionality` in v18 |

---

## Future: GPU (requires IOKit)

When implemented:
```python
gpu_time: MetricValue       # accumulatedGPUTime from AGXDeviceUserClient
gpu_rate: MetricValue       # GPU time/sec (calculated)
```
