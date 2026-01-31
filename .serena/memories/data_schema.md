# Unified Data Schema

> **Philosophy: One schema for everything.**
>
> ProcessScore is THE canonical data representation used across the entire **Rogue Hunter** application. Collector creates it, ring buffer stores it, socket broadcasts it, TUI displays it, storage persists it. 
>
> **DO NOT create alternative representations.** If you need process data anywhere in the application, use ProcessScore. No subsetting, no reshaping, no "simplified versions." One schema, everywhere.

**Last updated:** 2026-01-31
**Schema version:** 14

## Rogue Hunter Scoring

ProcessScore includes a 4-category scoring system that identifies different types of rogue behavior:
- **Blocking (40%)**: Causes I/O bottlenecks, memory thrashing
- **Contention (30%)**: Fights for CPU, scheduler pressure
- **Pressure (20%)**: Stresses memory, kernel resources
- **Efficiency (10%)**: Wastes resources through poor execution

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
    # Scoring (4-category system)
    # ─────────────────────────────────────────────────────────────
    score: MetricValue              # Final weighted score 0-100
    band: MetricValueStr            # low/medium/elevated/high/critical
    blocking_score: MetricValue     # 0-100, causes pauses (40% of final)
    contention_score: MetricValue   # 0-100, fighting for resources (30% of final)
    pressure_score: MetricValue     # 0-100, stressing system (20% of final)
    efficiency_score: MetricValue   # 0-100, wasting resources (10% of final)
    dominant_category: str          # "blocking" | "contention" | "pressure" | "efficiency"
    dominant_metrics: list[str]     # ["pageins:847/s", "disk:42M/s"]
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
| **Scoring** | score, band, blocking_score, contention_score, pressure_score, efficiency_score, dominant_category, dominant_metrics | 6 MetricValue + MetricValueStr + str + list |

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
| score | Calculated | 4-category weighted formula |
| band | Derived | From score via band thresholds |
| blocking_score | Calculated | Weighted: pageins_rate, disk_io_rate, faults_rate |
| contention_score | Calculated | Weighted: runnable_time_rate, csw_rate, cpu, qos_interactive_rate |
| pressure_score | Calculated | Weighted: mem, wakeups_rate, syscalls_rate, mach_msgs_rate |
| efficiency_score | Calculated | Weighted: IPC penalty, threads |
| dominant_category | Derived | Category with highest score |
| dominant_metrics | Derived | Top metrics in dominant category |

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
| blocking_score | Weighted sum (see Scoring Algorithm) |
| contention_score | Weighted sum |
| pressure_score | Weighted sum |
| efficiency_score | Weighted sum with IPC penalty |
| score | 0.4×blocking + 0.3×contention + 0.2×pressure + 0.1×efficiency |
| band | Score → threshold mapping |
| dominant_category | argmax(blocking, contention, pressure, efficiency) |
| dominant_metrics | Top 3 metrics in dominant category |

### Scoring Algorithm (4-category system)

**Blocking Score (40% of final) — Causes pauses:**
```
if state == "stuck": 100.0
else: pageins_rate/100 × 35 + disk_io_rate/100M × 35 + faults_rate/10k × 30
```

**Contention Score (30% of final) — Fighting for resources:**
```
runnable_time_rate/100 × 30 + csw_rate/10k × 30 + cpu/100 × 25 + qos_interactive_rate/100 × 15
```

**Pressure Score (20% of final) — Stressing system:**
```
mem/8GB × 35 + wakeups_rate/1k × 25 + syscalls_rate/100k × 20 + mach_msgs_rate/10k × 20
```

**Efficiency Score (10% of final) — Wasting resources:**
```
ipc_penalty × has_cycles × 60 + threads/100 × 40
(ipc_penalty = max(0, 1 - ipc/0.5) if ipc < 0.5 else 0)
```

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
  
  "score": {"current": 65, "low": 30, "high": 85},
  "band": {"current": "elevated", "low": "low", "high": "high"},
  "blocking_score": {"current": 58.0, "low": 10.0, "high": 72.0},
  "contention_score": {"current": 42.0, "low": 15.0, "high": 55.0},
  "pressure_score": {"current": 28.0, "low": 8.0, "high": 35.0},
  "efficiency_score": {"current": 12.0, "low": 5.0, "high": 18.0},
  "dominant_category": "blocking",
  "dominant_metrics": ["pageins:12/s", "disk:1.0M/s"]
}
```

---

## Dropped Fields

| Field | Reason |
|-------|--------|
| cmprs | Not available from libproc, was always 0 |
| sysbsd | Renamed to `syscalls`, now includes mach + unix |
| categories | Replaced by `dominant_category` + `dominant_metrics` in v14 |

---

## Future: GPU (requires IOKit)

When implemented:
```python
gpu_time: MetricValue       # accumulatedGPUTime from AGXDeviceUserClient
gpu_rate: MetricValue       # GPU time/sec (calculated)
```
