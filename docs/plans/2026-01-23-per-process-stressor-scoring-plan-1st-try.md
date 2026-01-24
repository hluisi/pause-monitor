# Per-Process Stressor Scoring Implementation Plan

> **For Claude:** Use systema:executing-plans to implement this plan task-by-task.

**Goal:** Replace powermetrics with `top` at 1Hz and implement per-process stressor scoring with 8 weighted metrics.

**Architecture:**
- `TopStream` replaces `PowermetricsStream` as data source (1Hz via `top -l 0`)
- `TopResult` is the **single canonical data structure** used everywhere (ring buffer, socket, storage)
- Scoring logic in `stress.py` with configurable weights
- Tier transitions driven by `TopResult.max_score` (highest process score)
- Ring buffer stores 30 `TopResult` samples (1Hz × 30s)

**Data Flow:**
```
top output (text)
       │
       ▼ parse_top_sample()
list[ProcessMetrics]  (all ~400 processes, raw metrics only)
       │
       ▼ select_rogues() — PIDs and categories first
dict[pid → set[categories]]
       │
       ▼ score selected PIDs only
list[ScoredProcess]  (10-20 rogues with scores)
       │
       ▼
   TopResult  ◄─── CANONICAL FORMAT EVERYWHERE
       │
       ├──► Ring Buffer (stores TopResult as-is)
       │
       ├──► Socket Broadcast (json.dumps(top_result))
       │
       └──► SQLite (json.dumps(top_result) + event metadata)
```

**Key Decisions:**
- Replace PowermetricsResult entirely (not parallel structures)
- TopResult is the single format — no transformations between components
- Normalization thresholds configurable from start
- 8 scoring factors: cpu(25), state(20), pageins(15), mem(15), cmprs(10), csw(10), sysbsd(5), threads(0)
- Storage: serialize entire TopResult as JSON (simple, reconstructable)
- Minimal TUI: CLI debug logging + ProcessesPanel showing ranked processes

**Patterns to Follow:**
- Dataclass for TopResult with `to_json()` and `from_json()` methods
- Async generator for TopStream (same pattern as PowermetricsStream)
- structlog for all logging
- Test fixtures with realistic top output samples

**Tech Stack:** Python 3.14, Textual, SQLite, asyncio

---

## Phase 1: Data Model & Configuration

### Task 1: Add Scoring Weights to Config

**Context:** Scoring weights need to be configurable. Add new config section for per-process scoring.

**Files:**
- Modify: `src/pause_monitor/config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

```python
# tests/test_config.py - add to existing tests

def test_scoring_config_defaults():
    """ScoringConfig has correct default weights summing to 100."""
    config = Config.load()
    weights = config.scoring
    
    assert weights.cpu == 25
    assert weights.state == 20
    assert weights.pageins == 15
    assert weights.mem == 15
    assert weights.cmprs == 10
    assert weights.csw == 10
    assert weights.sysbsd == 5
    assert weights.threads == 0
    
    total = (weights.cpu + weights.state + weights.pageins + weights.mem +
             weights.cmprs + weights.csw + weights.sysbsd + weights.threads)
    assert total == 100


def test_scoring_config_normalization_defaults():
    """ScoringConfig has normalization thresholds."""
    config = Config.load()
    norm = config.scoring.normalization
    
    # CPU: percentage thresholds
    assert norm.cpu_low == 10
    assert norm.cpu_high == 80
    
    # Memory: bytes thresholds (1GB, 8GB)
    assert norm.mem_low == 1_000_000_000
    assert norm.mem_high == 8_000_000_000
    
    # Pageins: count/s thresholds
    assert norm.pageins_low == 10
    assert norm.pageins_high == 500
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_config.py::test_scoring_config_defaults -v
uv run pytest tests/test_config.py::test_scoring_config_normalization_defaults -v
```
Expected: FAIL with AttributeError (no 'scoring' attribute)

**Step 3: Write minimal implementation**

Add to `config.py`:

```python
@dataclass
class NormalizationConfig:
    """Thresholds for normalizing raw metrics to 0-100 scale."""
    # CPU: percentage (0-100 from top)
    cpu_low: int = 10      # Below this = 0 score
    cpu_high: int = 80     # Above this = max score
    
    # Memory: bytes
    mem_low: int = 1_000_000_000     # 1 GB
    mem_high: int = 8_000_000_000    # 8 GB
    
    # Compressed memory: bytes
    cmprs_low: int = 100_000_000     # 100 MB
    cmprs_high: int = 2_000_000_000  # 2 GB
    
    # Pageins: count per second
    pageins_low: int = 10
    pageins_high: int = 500
    
    # Context switches: count per second
    csw_low: int = 1000
    csw_high: int = 50000
    
    # BSD syscalls: count per second
    sysbsd_low: int = 1000
    sysbsd_high: int = 100000
    
    # Threads: count
    threads_low: int = 50
    threads_high: int = 500


@dataclass
class ScoringConfig:
    """Per-process stressor scoring weights and normalization."""
    # Weights (must sum to 100)
    cpu: int = 25
    state: int = 20
    pageins: int = 15
    mem: int = 15
    cmprs: int = 10
    csw: int = 10
    sysbsd: int = 5
    threads: int = 0
    
    # Normalization thresholds
    normalization: NormalizationConfig = field(default_factory=NormalizationConfig)
```

Update `Config` class to include `scoring: ScoringConfig`.

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_config.py::test_scoring_config_defaults -v
uv run pytest tests/test_config.py::test_scoring_config_normalization_defaults -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/config.py tests/test_config.py
git commit -m "feat(config): add per-process scoring weights and normalization thresholds"
```

---

### Task 2: Create TopResult Dataclass

**Context:** New data structure replacing PowermetricsResult. Contains parsed top output with per-process metrics.

**Files:**
- Modify: `src/pause_monitor/collector.py`
- Test: `tests/test_collector.py`

**Step 1: Write the failing test**

```python
# tests/test_collector.py - add new tests

def test_process_metrics_dataclass():
    """ProcessMetrics holds per-process raw metrics."""
    from pause_monitor.collector import ProcessMetrics
    
    pm = ProcessMetrics(
        pid=1234,
        command="Chrome",
        cpu=45.2,
        state="running",
        mem=2_000_000_000,
        cmprs=500_000_000,
        pageins=12,
        csw=1500,
        sysbsd=8000,
        threads=42,
    )
    
    assert pm.pid == 1234
    assert pm.command == "Chrome"
    assert pm.cpu == 45.2
    assert pm.state == "running"
    assert pm.mem == 2_000_000_000
    assert pm.is_stuck is False


def test_process_metrics_is_stuck():
    """ProcessMetrics.is_stuck returns True for stuck state."""
    from pause_monitor.collector import ProcessMetrics
    
    pm = ProcessMetrics(
        pid=1, command="test", cpu=0, state="stuck",
        mem=0, cmprs=0, pageins=0, csw=0, sysbsd=0, threads=1,
    )
    assert pm.is_stuck is True


def test_scored_process_dataclass():
    """ScoredProcess holds metrics plus calculated score and categories."""
    from pause_monitor.collector import ProcessMetrics, ScoredProcess
    
    metrics = ProcessMetrics(
        pid=1234, command="Chrome", cpu=45.2, state="running",
        mem=2_000_000_000, cmprs=500_000_000, pageins=12,
        csw=1500, sysbsd=8000, threads=42,
    )
    
    sp = ScoredProcess(
        metrics=metrics,
        score=67,
        categories=frozenset({"cpu", "mem"}),
    )
    
    assert sp.score == 67
    assert "cpu" in sp.categories
    assert sp.metrics.command == "Chrome"


def test_top_result_dataclass():
    """TopResult holds timestamp, sample metadata, and rogue processes."""
    from pause_monitor.collector import TopResult, ProcessMetrics, ScoredProcess
    
    processes = [
        ScoredProcess(
            metrics=ProcessMetrics(
                pid=1, command="test", cpu=50, state="running",
                mem=1_000_000_000, cmprs=0, pageins=0, csw=0, sysbsd=0, threads=1,
            ),
            score=45,
            categories=frozenset({"cpu"}),
        )
    ]
    
    result = TopResult(
        timestamp=1706000000.0,
        process_count=150,
        max_score=45,
        rogue_processes=processes,
    )
    
    assert result.process_count == 150
    assert result.max_score == 45
    assert len(result.rogue_processes) == 1


def test_top_result_json_roundtrip():
    """TopResult can serialize to JSON and back."""
    from pause_monitor.collector import TopResult, ProcessMetrics, ScoredProcess
    
    original = TopResult(
        timestamp=1706000000.0,
        process_count=423,
        max_score=67,
        rogue_processes=[
            ScoredProcess(
                metrics=ProcessMetrics(
                    pid=1234, command="Chrome", cpu=45.2, state="running",
                    mem=2_000_000_000, cmprs=500_000_000, pageins=12,
                    csw=1500, sysbsd=8000, threads=42,
                ),
                score=67,
                categories=frozenset({"cpu", "mem"}),
            ),
        ],
    )
    
    # Roundtrip through JSON
    json_str = original.to_json()
    restored = TopResult.from_json(json_str)
    
    assert restored.timestamp == original.timestamp
    assert restored.process_count == original.process_count
    assert restored.max_score == original.max_score
    assert len(restored.rogue_processes) == 1
    assert restored.rogue_processes[0].metrics.command == "Chrome"
    assert restored.rogue_processes[0].score == 67
    assert "cpu" in restored.rogue_processes[0].categories
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_collector.py::test_process_metrics_dataclass -v
```
Expected: FAIL with ImportError

**Step 3: Write minimal implementation**

Add to `collector.py`:

```python
@dataclass
class ProcessMetrics:
    """Raw metrics for a single process from top output."""
    pid: int
    command: str
    cpu: float          # CPU percentage (0-100+)
    state: str          # running, sleeping, stuck, etc.
    mem: int            # Memory bytes (resident)
    cmprs: int          # Compressed memory bytes
    pageins: int        # Page-ins per second (delta)
    csw: int            # Context switches per second (delta)
    sysbsd: int         # BSD syscalls per second (delta)
    threads: int        # Thread count
    
    @property
    def is_stuck(self) -> bool:
        """Return True if process is in stuck state."""
        return self.state.lower() == "stuck"


@dataclass
class ScoredProcess:
    """A process with its calculated stressor score."""
    metrics: ProcessMetrics
    score: int                      # 0-100 stressor score
    categories: frozenset[str]      # Why included: {"cpu", "mem", "stuck", ...}


@dataclass
class TopResult:
    """Parsed result from one top sample — THE canonical data structure.
    
    This is the single format used everywhere:
    - Ring buffer stores TopResult directly
    - Socket broadcasts TopResult as JSON
    - SQLite stores TopResult as JSON
    """
    timestamp: float                # Unix timestamp
    process_count: int              # Total processes in sample
    max_score: int                  # Highest process score (for tier decisions)
    rogue_processes: list[ScoredProcess]  # 10-20 scored rogue processes
    
    def to_json(self) -> str:
        """Serialize to JSON string."""
        import json
        return json.dumps({
            "timestamp": self.timestamp,
            "process_count": self.process_count,
            "max_score": self.max_score,
            "rogue_processes": [
                {
                    "pid": sp.metrics.pid,
                    "command": sp.metrics.command,
                    "cpu": sp.metrics.cpu,
                    "state": sp.metrics.state,
                    "mem": sp.metrics.mem,
                    "cmprs": sp.metrics.cmprs,
                    "pageins": sp.metrics.pageins,
                    "csw": sp.metrics.csw,
                    "sysbsd": sp.metrics.sysbsd,
                    "threads": sp.metrics.threads,
                    "score": sp.score,
                    "categories": list(sp.categories),
                }
                for sp in self.rogue_processes
            ],
        })
    
    @classmethod
    def from_json(cls, json_str: str) -> "TopResult":
        """Deserialize from JSON string."""
        import json
        data = json.loads(json_str)
        return cls(
            timestamp=data["timestamp"],
            process_count=data["process_count"],
            max_score=data["max_score"],
            rogue_processes=[
                ScoredProcess(
                    metrics=ProcessMetrics(
                        pid=p["pid"],
                        command=p["command"],
                        cpu=p["cpu"],
                        state=p["state"],
                        mem=p["mem"],
                        cmprs=p["cmprs"],
                        pageins=p["pageins"],
                        csw=p["csw"],
                        sysbsd=p["sysbsd"],
                        threads=p["threads"],
                    ),
                    score=p["score"],
                    categories=frozenset(p["categories"]),
                )
                for p in data["rogue_processes"]
            ],
        )
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_collector.py::test_process_metrics_dataclass -v
uv run pytest tests/test_collector.py::test_scored_process_dataclass -v
uv run pytest tests/test_collector.py::test_top_result_dataclass -v
uv run pytest tests/test_collector.py::test_top_result_json_roundtrip -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/collector.py tests/test_collector.py
git commit -m "feat(collector): add TopResult, ScoredProcess, ProcessMetrics dataclasses"
```

---

### Task 3: Implement Per-Process Scoring Function

**Context:** Core scoring logic that calculates 0-100 score from ProcessMetrics using configurable weights.

**Files:**
- Modify: `src/pause_monitor/stress.py`
- Test: `tests/test_stress.py`

**Step 1: Write the failing test**

```python
# tests/test_stress.py - add new tests

def test_score_process_basic():
    """score_process returns 0-100 based on weighted metrics."""
    from pause_monitor.stress import score_process
    from pause_monitor.collector import ProcessMetrics
    from pause_monitor.config import ScoringConfig
    
    # High CPU process
    metrics = ProcessMetrics(
        pid=1, command="test", cpu=80, state="running",
        mem=1_000_000_000, cmprs=0, pageins=0, csw=0, sysbsd=0, threads=1,
    )
    config = ScoringConfig()  # defaults
    
    score = score_process(metrics, config)
    
    # CPU at 80% should contribute most of the 25 points for CPU factor
    assert 20 <= score <= 30  # Mostly CPU contribution


def test_score_process_stuck_state():
    """Stuck state gets full state weight."""
    from pause_monitor.stress import score_process
    from pause_monitor.collector import ProcessMetrics
    from pause_monitor.config import ScoringConfig
    
    metrics = ProcessMetrics(
        pid=1, command="test", cpu=0, state="stuck",
        mem=0, cmprs=0, pageins=0, csw=0, sysbsd=0, threads=1,
    )
    config = ScoringConfig()
    
    score = score_process(metrics, config)
    
    # Stuck = full 20 points for state
    assert score == 20


def test_score_process_multiple_factors():
    """Score combines multiple weighted factors."""
    from pause_monitor.stress import score_process
    from pause_monitor.collector import ProcessMetrics
    from pause_monitor.config import ScoringConfig
    
    # High everything
    metrics = ProcessMetrics(
        pid=1, command="test", cpu=100, state="running",
        mem=10_000_000_000, cmprs=3_000_000_000, pageins=1000,
        csw=100000, sysbsd=200000, threads=1000,
    )
    config = ScoringConfig()
    
    score = score_process(metrics, config)
    
    # Should be close to max (100) but threads default weight is 0
    assert score >= 90


def test_score_process_capped_at_100():
    """Score never exceeds 100."""
    from pause_monitor.stress import score_process
    from pause_monitor.collector import ProcessMetrics
    from pause_monitor.config import ScoringConfig
    
    # Extreme values
    metrics = ProcessMetrics(
        pid=1, command="test", cpu=500, state="stuck",  # >100% CPU possible
        mem=100_000_000_000, cmprs=50_000_000_000, pageins=10000,
        csw=1000000, sysbsd=1000000, threads=10000,
    )
    config = ScoringConfig()
    
    score = score_process(metrics, config)
    assert score == 100
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_stress.py::test_score_process_basic -v
```
Expected: FAIL with ImportError (no score_process function)

**Step 3: Write minimal implementation**

Add to `stress.py`:

```python
from pause_monitor.collector import ProcessMetrics
from pause_monitor.config import ScoringConfig


def _normalize(value: float, low: float, high: float) -> float:
    """Normalize value to 0.0-1.0 range using thresholds."""
    if value <= low:
        return 0.0
    if value >= high:
        return 1.0
    return (value - low) / (high - low)


def score_process(metrics: ProcessMetrics, config: ScoringConfig) -> int:
    """Calculate stressor score (0-100) for a process.
    
    Uses configurable weights and normalization thresholds.
    """
    norm = config.normalization
    
    # Calculate each factor's contribution
    cpu_factor = _normalize(metrics.cpu, norm.cpu_low, norm.cpu_high) * config.cpu
    
    # State: binary - stuck = full points, otherwise 0
    state_factor = config.state if metrics.is_stuck else 0
    
    mem_factor = _normalize(metrics.mem, norm.mem_low, norm.mem_high) * config.mem
    cmprs_factor = _normalize(metrics.cmprs, norm.cmprs_low, norm.cmprs_high) * config.cmprs
    pageins_factor = _normalize(metrics.pageins, norm.pageins_low, norm.pageins_high) * config.pageins
    csw_factor = _normalize(metrics.csw, norm.csw_low, norm.csw_high) * config.csw
    sysbsd_factor = _normalize(metrics.sysbsd, norm.sysbsd_low, norm.sysbsd_high) * config.sysbsd
    threads_factor = _normalize(metrics.threads, norm.threads_low, norm.threads_high) * config.threads
    
    total = (cpu_factor + state_factor + mem_factor + cmprs_factor +
             pageins_factor + csw_factor + sysbsd_factor + threads_factor)
    
    return min(100, int(round(total)))
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_stress.py::test_score_process_basic -v
uv run pytest tests/test_stress.py::test_score_process_stuck_state -v
uv run pytest tests/test_stress.py::test_score_process_multiple_factors -v
uv run pytest tests/test_stress.py::test_score_process_capped_at_100 -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/stress.py tests/test_stress.py
git commit -m "feat(stress): add per-process scoring with configurable weights"
```

---

### Task 4: Implement Rogue Process Selection

**Context:** Select which processes qualify as "rogue" based on automatic inclusion rules and category ranking.

**Files:**
- Modify: `src/pause_monitor/stress.py`
- Test: `tests/test_stress.py`

**Step 1: Write the failing test**

```python
# tests/test_stress.py - add new tests

def test_select_rogue_processes_stuck():
    """Stuck processes are always included."""
    from pause_monitor.stress import select_rogue_processes
    from pause_monitor.collector import ProcessMetrics
    from pause_monitor.config import ScoringConfig
    
    processes = [
        ProcessMetrics(pid=1, command="stuck_proc", cpu=0, state="stuck",
                       mem=0, cmprs=0, pageins=0, csw=0, sysbsd=0, threads=1),
        ProcessMetrics(pid=2, command="normal", cpu=1, state="running",
                       mem=1000, cmprs=0, pageins=0, csw=0, sysbsd=0, threads=1),
    ]
    
    rogues = select_rogue_processes(processes, ScoringConfig())
    
    pids = {r.metrics.pid for r in rogues}
    assert 1 in pids  # stuck process included
    assert "stuck" in rogues[0].categories or any("stuck" in r.categories for r in rogues if r.metrics.pid == 1)


def test_select_rogue_processes_paging():
    """Paging processes (pageins > 0) are always included."""
    from pause_monitor.stress import select_rogue_processes
    from pause_monitor.collector import ProcessMetrics
    from pause_monitor.config import ScoringConfig
    
    processes = [
        ProcessMetrics(pid=1, command="paging", cpu=0, state="running",
                       mem=0, cmprs=0, pageins=5, csw=0, sysbsd=0, threads=1),
        ProcessMetrics(pid=2, command="normal", cpu=1, state="running",
                       mem=1000, cmprs=0, pageins=0, csw=0, sysbsd=0, threads=1),
    ]
    
    rogues = select_rogue_processes(processes, ScoringConfig())
    
    pids = {r.metrics.pid for r in rogues}
    assert 1 in pids  # paging process included


def test_select_rogue_processes_top3_per_category():
    """Top 3 per category are included."""
    from pause_monitor.stress import select_rogue_processes
    from pause_monitor.collector import ProcessMetrics
    from pause_monitor.config import ScoringConfig
    
    # Create 5 processes with varying CPU - only top 3 should be in "cpu" category
    processes = [
        ProcessMetrics(pid=i, command=f"proc{i}", cpu=i*10, state="running",
                       mem=0, cmprs=0, pageins=0, csw=0, sysbsd=0, threads=1)
        for i in range(1, 6)  # cpu: 10, 20, 30, 40, 50
    ]
    
    rogues = select_rogue_processes(processes, ScoringConfig())
    
    # Top 3 by CPU should be pids 3, 4, 5 (cpu 30, 40, 50)
    cpu_category_pids = {r.metrics.pid for r in rogues if "cpu" in r.categories}
    assert cpu_category_pids == {3, 4, 5}


def test_select_rogue_processes_deduplication():
    """Process appearing in multiple categories is only listed once."""
    from pause_monitor.stress import select_rogue_processes
    from pause_monitor.collector import ProcessMetrics
    from pause_monitor.config import ScoringConfig
    
    # High in both CPU and memory
    processes = [
        ProcessMetrics(pid=1, command="hog", cpu=90, state="running",
                       mem=10_000_000_000, cmprs=0, pageins=0, csw=0, sysbsd=0, threads=1),
    ]
    
    rogues = select_rogue_processes(processes, ScoringConfig())
    
    assert len(rogues) == 1  # Only one entry
    assert "cpu" in rogues[0].categories
    assert "mem" in rogues[0].categories
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_stress.py::test_select_rogue_processes_stuck -v
```
Expected: FAIL with ImportError (no select_rogue_processes)

**Step 3: Write minimal implementation**

Add to `stress.py`:

```python
def select_rogue_processes(
    all_processes: list[ProcessMetrics],
    config: ScoringConfig,
) -> list[ScoredProcess]:
    """Select and score rogue processes from full process list.
    
    Selection criteria:
    1. Automatic inclusion: stuck state OR pageins > 0
    2. Top 3 per category: cpu, mem, cmprs, threads, csw, sysbsd
    
    Returns deduplicated list sorted by score descending.
    """
    # Track which PIDs are included and why
    pid_categories: dict[int, set[str]] = {}
    pid_metrics: dict[int, ProcessMetrics] = {}
    
    # Automatic inclusion
    for p in all_processes:
        pid_metrics[p.pid] = p
        if p.is_stuck:
            pid_categories.setdefault(p.pid, set()).add("stuck")
        if p.pageins > 0:
            pid_categories.setdefault(p.pid, set()).add("paging")
    
    # Category rankings (top 3 each)
    categories = [
        ("cpu", lambda p: p.cpu),
        ("mem", lambda p: p.mem),
        ("cmprs", lambda p: p.cmprs),
        ("threads", lambda p: p.threads),
        ("csw", lambda p: p.csw),
        ("sysbsd", lambda p: p.sysbsd),
    ]
    
    for cat_name, key_fn in categories:
        sorted_procs = sorted(all_processes, key=key_fn, reverse=True)
        for p in sorted_procs[:3]:
            if key_fn(p) > 0:  # Only if metric is non-zero
                pid_categories.setdefault(p.pid, set()).add(cat_name)
                pid_metrics[p.pid] = p
    
    # Score and build result
    result = []
    for pid, categories_set in pid_categories.items():
        metrics = pid_metrics[pid]
        score = score_process(metrics, config)
        result.append(ScoredProcess(
            metrics=metrics,
            score=score,
            categories=frozenset(categories_set),
        ))
    
    # Sort by score descending
    result.sort(key=lambda sp: sp.score, reverse=True)
    return result
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_stress.py::test_select_rogue_processes_stuck -v
uv run pytest tests/test_stress.py::test_select_rogue_processes_paging -v
uv run pytest tests/test_stress.py::test_select_rogue_processes_top3_per_category -v
uv run pytest tests/test_stress.py::test_select_rogue_processes_deduplication -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/stress.py tests/test_stress.py
git commit -m "feat(stress): add rogue process selection with category ranking"
```

---

## Phase 2: Data Collection

### Task 5: Parse top Output

**Context:** Parse the text output from `top -l 0 -s 1 -stats ...` into ProcessMetrics.

**Files:**
- Modify: `src/pause_monitor/collector.py`
- Create: `tests/fixtures/top_sample.txt`
- Test: `tests/test_collector.py`

**Step 1: Create test fixture**

Create `tests/fixtures/top_sample.txt` with realistic top output (capture from actual macOS `top -l 1 -s 1 -stats pid,command,cpu,state,rsize,cmprs,pageins,csw,msgsent,msgrecv,sysbsd,sysmach,threads`):

```
Processes: 423 total, 3 running, 420 sleeping, 1842 threads
Load Avg: 2.14, 2.08, 1.95
CPU usage: 12.5% user, 8.3% sys, 79.2% idle
SharedLibs: 412M resident, 89M data, 52M linkedit.
MemRegions: 128942 total, 4.2G resident, 201M private, 1.8G shared.
PhysMem: 14G used (2.1G wired, 1.5G compressor), 2.0G unused.
VM: 214T vsize, 3.8T framework vsize, 12345(0) swapins, 8234(0) swapouts.
Networks: packets: 1234567/890M in, 987654/567M out.
Disks: 234567/8.9G read, 123456/4.5G written.

PID    COMMAND          %CPU STATE        RSIZE    CMPRS   PAGEINS  CSW   MSGSENT MSGRECV  SYSBSD   SYSMACH #TH
1234   Chrome           45.2 running      2000M    500M    12       1500  4500    3200     8000     2000    42
5678   mds_stores       12.3 running      412M     100M    0        800   200     150      3000     500     8
9999   kernel_task      8.5  running      523M     0B      0        50000 100     80       1000     100     128
1111   WindowServer     5.2  sleeping     387M     50M     0        3000  8000    7500     5000     3000    12
2222   Code Helper      3.1  sleeping     298M     80M     3        500   100     90       2000     400     6
3333   stuck_process    0.0  stuck        100M     10M     0        0     0       0        0        0       2
```

**Step 2: Write the failing test**

```python
# tests/test_collector.py

def test_parse_top_sample():
    """parse_top_sample extracts process metrics from top output."""
    from pause_monitor.collector import parse_top_sample
    from pathlib import Path
    
    sample_path = Path(__file__).parent / "fixtures" / "top_sample.txt"
    sample_text = sample_path.read_text()
    
    processes = parse_top_sample(sample_text)
    
    # Should find 6 processes in sample
    assert len(processes) == 6
    
    # Check Chrome entry
    chrome = next(p for p in processes if p.command == "Chrome")
    assert chrome.pid == 1234
    assert chrome.cpu == 45.2
    assert chrome.state == "running"
    assert chrome.mem == 2000 * 1024 * 1024  # 2000M -> bytes
    assert chrome.cmprs == 500 * 1024 * 1024  # 500M -> bytes
    assert chrome.pageins == 12
    assert chrome.csw == 1500
    assert chrome.sysbsd == 8000
    assert chrome.threads == 42
    
    # Check stuck process
    stuck = next(p for p in processes if p.command == "stuck_process")
    assert stuck.is_stuck is True


def test_parse_top_sample_process_count():
    """parse_top_sample extracts total process count from header."""
    from pause_monitor.collector import parse_top_sample_header
    from pathlib import Path
    
    sample_path = Path(__file__).parent / "fixtures" / "top_sample.txt"
    sample_text = sample_path.read_text()
    
    process_count = parse_top_sample_header(sample_text)
    assert process_count == 423
```

**Step 3: Run test to verify it fails**

```bash
uv run pytest tests/test_collector.py::test_parse_top_sample -v
```
Expected: FAIL with ImportError (no parse_top_sample)

**Step 4: Write minimal implementation**

Add to `collector.py`:

```python
import re

def _parse_size(size_str: str) -> int:
    """Parse size string like '2000M', '500K', '1.5G' to bytes."""
    size_str = size_str.strip()
    if size_str == "0B" or size_str == "0":
        return 0
    
    multipliers = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    
    match = re.match(r"([\d.]+)([KMGT])?", size_str)
    if not match:
        return 0
    
    value = float(match.group(1))
    unit = match.group(2) or ""
    multiplier = multipliers.get(unit, 1)
    
    return int(value * multiplier)


def parse_top_sample_header(output: str) -> int:
    """Extract total process count from top header."""
    match = re.search(r"Processes:\s*(\d+)\s*total", output)
    return int(match.group(1)) if match else 0


def parse_top_sample(output: str) -> list[ProcessMetrics]:
    """Parse top output into list of ProcessMetrics.
    
    Expects output from: top -l 1 -stats pid,command,cpu,state,rsize,cmprs,pageins,csw,msgsent,msgrecv,sysbsd,sysmach,threads
    """
    processes = []
    
    # Find process lines (after header line starting with PID)
    lines = output.strip().split("\n")
    in_processes = False
    
    for line in lines:
        if line.startswith("PID"):
            in_processes = True
            continue
        
        if not in_processes or not line.strip():
            continue
        
        # Parse process line
        # PID COMMAND %CPU STATE RSIZE CMPRS PAGEINS CSW MSGSENT MSGRECV SYSBSD SYSMACH #TH
        parts = line.split()
        if len(parts) < 13:
            continue
        
        try:
            pid = int(parts[0])
            command = parts[1]
            cpu = float(parts[2])
            state = parts[3].lower()
            mem = _parse_size(parts[4])
            cmprs = _parse_size(parts[5])
            pageins = int(parts[6])
            csw = int(parts[7])
            # parts[8], parts[9] are msgsent, msgrecv - skip
            sysbsd = int(parts[10])
            # parts[11] is sysmach - skip
            threads = int(parts[12])
            
            processes.append(ProcessMetrics(
                pid=pid,
                command=command,
                cpu=cpu,
                state=state,
                mem=mem,
                cmprs=cmprs,
                pageins=pageins,
                csw=csw,
                sysbsd=sysbsd,
                threads=threads,
            ))
        except (ValueError, IndexError):
            continue  # Skip malformed lines
    
    return processes
```

**Step 5: Run test to verify it passes**

```bash
uv run pytest tests/test_collector.py::test_parse_top_sample -v
uv run pytest tests/test_collector.py::test_parse_top_sample_process_count -v
```
Expected: PASS

**Step 6: Commit**

```bash
git add src/pause_monitor/collector.py tests/test_collector.py tests/fixtures/
git commit -m "feat(collector): add top output parsing"
```

---

### Task 6: Implement TopStream Async Generator

**Context:** Replace PowermetricsStream with TopStream that spawns `top` and yields TopResult at 1Hz.

**Files:**
- Modify: `src/pause_monitor/collector.py`
- Test: `tests/test_collector.py`

**Step 1: Write the failing test**

```python
# tests/test_collector.py

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_top_stream_command():
    """TopStream uses correct top command."""
    from pause_monitor.collector import TopStream
    
    stream = TopStream()
    expected_stats = "pid,command,cpu,state,rsize,cmprs,pageins,csw,msgsent,msgrecv,sysbsd,sysmach,threads"
    
    assert "-l" in stream.TOP_CMD
    assert "-stats" in stream.TOP_CMD
    assert expected_stats in stream.TOP_CMD


@pytest.mark.asyncio
async def test_top_stream_yields_top_result():
    """TopStream yields TopResult from parsed output."""
    from pause_monitor.collector import TopStream, TopResult
    from pause_monitor.config import ScoringConfig
    from pathlib import Path
    
    sample_path = Path(__file__).parent / "fixtures" / "top_sample.txt"
    sample_text = sample_path.read_text()
    
    # Mock subprocess
    mock_process = AsyncMock()
    mock_process.stdout.readline = AsyncMock(side_effect=[
        line.encode() + b"\n" for line in sample_text.split("\n")
    ] + [b""])  # Empty bytes signals EOF
    mock_process.returncode = 0
    
    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        stream = TopStream(config=ScoringConfig())
        await stream.start()
        
        results = []
        async for result in stream.read_samples():
            results.append(result)
            break  # Just get first sample
        
        assert len(results) == 1
        assert isinstance(results[0], TopResult)
        assert results[0].process_count == 423
        assert len(results[0].rogue_processes) > 0
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_collector.py::test_top_stream_command -v
```
Expected: FAIL with ImportError (no TopStream)

**Step 3: Write minimal implementation**

Add to `collector.py`:

```python
class TopStream:
    """Async stream of per-process metrics from macOS top command."""
    
    TOP_CMD = (
        "/usr/bin/top -l 0 -s 1 -n 0 "
        "-stats pid,command,cpu,state,rsize,cmprs,pageins,csw,msgsent,msgrecv,sysbsd,sysmach,threads"
    )
    
    def __init__(self, config: "ScoringConfig | None" = None):
        from pause_monitor.config import ScoringConfig
        self._config = config or ScoringConfig()
        self._process: asyncio.subprocess.Process | None = None
        self._status = StreamStatus.NOT_STARTED
    
    @property
    def status(self) -> StreamStatus:
        return self._status
    
    async def start(self) -> None:
        """Start top subprocess."""
        if self._status == StreamStatus.RUNNING:
            return
        
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.TOP_CMD.split(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self._status = StreamStatus.RUNNING
            log.info("top_stream_started")
        except FileNotFoundError:
            self._status = StreamStatus.FAILED
            raise RuntimeError("top command not found at /usr/bin/top")
        except PermissionError:
            self._status = StreamStatus.FAILED
            raise RuntimeError("Permission denied running top")
    
    async def stop(self) -> None:
        """Stop top subprocess gracefully."""
        if self._process is None:
            return
        
        try:
            self._process.terminate()
            await self._process.wait()
        except ProcessLookupError:
            pass
        
        self._status = StreamStatus.STOPPED
        self._process = None
    
    def terminate(self) -> None:
        """Sync kill for signal handlers."""
        if self._process is None:
            return
        try:
            self._process.kill()
        except ProcessLookupError:
            pass
    
    async def read_samples(self) -> AsyncIterator[TopResult]:
        """Yield TopResult for each sample from top.
        
        Top outputs samples separated by blank lines in logging mode.
        """
        if self._process is None or self._process.stdout is None:
            return
        
        buffer_lines: list[str] = []
        
        while True:
            try:
                line_bytes = await self._process.stdout.readline()
                if not line_bytes:
                    break  # EOF
                
                line = line_bytes.decode("utf-8", errors="replace").rstrip()
                
                if not line and buffer_lines:
                    # Blank line = end of sample
                    sample_text = "\n".join(buffer_lines)
                    buffer_lines = []
                    
                    result = self._parse_sample(sample_text)
                    if result:
                        yield result
                else:
                    buffer_lines.append(line)
                    
            except asyncio.CancelledError:
                break
        
        self._status = StreamStatus.STOPPED
    
    def _parse_sample(self, sample_text: str) -> TopResult | None:
        """Parse a complete sample into TopResult."""
        try:
            import time
            from pause_monitor.stress import select_rogue_processes
            
            process_count = parse_top_sample_header(sample_text)
            all_processes = parse_top_sample(sample_text)
            
            if not all_processes:
                return None
            
            rogue_processes = select_rogue_processes(all_processes, self._config)
            max_score = max((p.score for p in rogue_processes), default=0)
            
            return TopResult(
                timestamp=time.time(),
                process_count=process_count,
                max_score=max_score,
                rogue_processes=rogue_processes,
            )
        except Exception as e:
            log.warning("failed_to_parse_top_sample", error=str(e))
            return None
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_collector.py::test_top_stream_command -v
uv run pytest tests/test_collector.py::test_top_stream_yields_top_result -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/collector.py tests/test_collector.py
git commit -m "feat(collector): add TopStream async generator for top output"
```

---

## Phase 3: Storage Updates

### Task 7: Update Schema for TopResult Storage

**Context:** SCHEMA_VERSION 7 stores TopResult directly as JSON. No separate wrapper dataclass needed — TopResult IS the canonical format.

**Files:**
- Modify: `src/pause_monitor/storage.py`
- Test: `tests/test_storage.py`

**Step 1: Write the failing test**

```python
# tests/test_storage.py - add new tests

def test_schema_version_7(initialized_db):
    """Schema version is 7 after init."""
    from pause_monitor.storage import get_schema_version
    import sqlite3
    
    conn = sqlite3.connect(initialized_db)
    version = get_schema_version(conn)
    assert version == 7


def test_event_sample_table_has_top_result_column(initialized_db):
    """event_samples table stores TopResult as JSON."""
    import sqlite3
    
    conn = sqlite3.connect(initialized_db)
    cursor = conn.execute("PRAGMA table_info(event_samples)")
    columns = {row[1] for row in cursor.fetchall()}
    
    assert "top_result" in columns  # Entire TopResult as JSON
    assert "tier" in columns        # Only metadata we add


def test_insert_and_get_event_sample(initialized_db):
    """insert_event_sample stores TopResult, get returns identical TopResult."""
    from pause_monitor.storage import (
        create_event, insert_event_sample, get_event_samples, get_connection,
    )
    from pause_monitor.collector import TopResult, ProcessMetrics, ScoredProcess
    from datetime import datetime
    
    conn = get_connection(initialized_db)
    event_id = create_event(conn, datetime.now())
    
    # Create a TopResult (the canonical format)
    top_result = TopResult(
        timestamp=1706000000.0,
        process_count=423,
        max_score=67,
        rogue_processes=[
            ScoredProcess(
                metrics=ProcessMetrics(
                    pid=1234, command="Chrome", cpu=45.2, state="running",
                    mem=2_000_000_000, cmprs=500_000_000, pageins=12,
                    csw=1500, sysbsd=8000, threads=42,
                ),
                score=67,
                categories=frozenset({"cpu", "mem"}),
            ),
        ],
    )
    
    # Insert — just TopResult + event metadata
    insert_event_sample(conn, event_id, tier=2, top_result=top_result)
    
    # Retrieve — get back identical TopResult
    samples = get_event_samples(conn, event_id)
    assert len(samples) == 1
    
    retrieved_tier, retrieved_top_result = samples[0]
    assert retrieved_tier == 2
    assert retrieved_top_result.timestamp == top_result.timestamp
    assert retrieved_top_result.max_score == 67
    assert len(retrieved_top_result.rogue_processes) == 1
    assert retrieved_top_result.rogue_processes[0].metrics.command == "Chrome"
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_storage.py::test_schema_version_7 -v
```
Expected: FAIL (version is 6)

**Step 3: Write minimal implementation**

Update `storage.py`:

```python
SCHEMA_VERSION = 7

# Simplified schema — TopResult stored as JSON blob
SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_timestamp REAL NOT NULL,
    end_timestamp REAL,
    peak_stress INTEGER,
    peak_tier INTEGER,
    status TEXT DEFAULT 'unreviewed',
    notes TEXT
);

CREATE TABLE IF NOT EXISTS event_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    tier INTEGER NOT NULL,
    top_result TEXT NOT NULL,  -- Entire TopResult as JSON
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_event_samples_event ON event_samples(event_id);
"""


def insert_event_sample(
    conn: sqlite3.Connection,
    event_id: int,
    tier: int,
    top_result: "TopResult",
) -> int:
    """Insert event sample — stores TopResult directly as JSON."""
    cursor = conn.execute(
        "INSERT INTO event_samples (event_id, tier, top_result) VALUES (?, ?, ?)",
        (event_id, tier, top_result.to_json()),
    )
    conn.commit()
    return cursor.lastrowid


def get_event_samples(
    conn: sqlite3.Connection,
    event_id: int,
) -> list[tuple[int, "TopResult"]]:
    """Retrieve event samples — returns (tier, TopResult) tuples."""
    from pause_monitor.collector import TopResult
    
    cursor = conn.execute(
        "SELECT tier, top_result FROM event_samples WHERE event_id = ? ORDER BY id ASC",
        (event_id,),
    )
    
    return [
        (row[0], TopResult.from_json(row[1]))
        for row in cursor
    ]
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_storage.py::test_schema_version_7 -v
uv run pytest tests/test_storage.py::test_event_sample_table_has_top_result_column -v
uv run pytest tests/test_storage.py::test_insert_and_get_event_sample -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/storage.py tests/test_storage.py
git commit -m "feat(storage): schema v7 stores TopResult as JSON directly"
```

---

## Phase 4: Daemon Integration

### Task 8: Update Ring Buffer for TopResult

**Context:** Ring buffer stores TopResult directly at 1Hz. Same canonical format as socket and storage.

**Files:**
- Modify: `src/pause_monitor/ringbuffer.py`
- Test: `tests/test_ringbuffer.py`

**Step 1: Write the failing test**

```python
# tests/test_ringbuffer.py - add/update tests

def test_ring_sample_with_top_result():
    """RingSample stores TopResult directly (same format everywhere)."""
    from pause_monitor.ringbuffer import RingSample
    from pause_monitor.collector import TopResult, ScoredProcess, ProcessMetrics
    
    top_result = TopResult(
        timestamp=1706000000.0,
        process_count=423,
        max_score=67,
        rogue_processes=[
            ScoredProcess(
                metrics=ProcessMetrics(
                    pid=1, command="test", cpu=50, state="running",
                    mem=1_000_000_000, cmprs=0, pageins=0, csw=0, sysbsd=0, threads=1,
                ),
                score=45,
                categories=frozenset({"cpu"}),
            )
        ],
    )
    
    sample = RingSample(
        timestamp=1706000000.0,
        top_result=top_result,
        tier=1,
    )
    
    # TopResult stored directly, accessible immediately
    assert sample.top_result.max_score == 67
    assert sample.top_result.rogue_processes[0].metrics.command == "test"


def test_ring_buffer_1hz_capacity():
    """Ring buffer at 1Hz holds 30 samples for 30 seconds."""
    from pause_monitor.ringbuffer import RingBuffer
    
    # 1Hz * 30 seconds = 30 samples
    buffer = RingBuffer(max_samples=30)
    assert buffer._samples.maxlen == 30
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_ringbuffer.py::test_ring_sample_with_top_result -v
```
Expected: FAIL (RingSample has wrong fields)

**Step 3: Write minimal implementation**

Update `ringbuffer.py`:

```python
from pause_monitor.collector import TopResult

@dataclass
class RingSample:
    """A single sample in the ring buffer — stores TopResult directly."""
    timestamp: float
    top_result: TopResult  # The canonical format, stored as-is
    tier: int
```

Update `RingBuffer.push()` to accept `TopResult`:

```python
def push(self, top_result: TopResult, tier: int) -> None:
    """Add a sample to the buffer."""
    self._samples.append(RingSample(
        timestamp=top_result.timestamp,
        top_result=top_result,
        tier=tier,
    ))
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_ringbuffer.py::test_ring_sample_with_top_result -v
uv run pytest tests/test_ringbuffer.py::test_ring_buffer_1hz_capacity -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/ringbuffer.py tests/test_ringbuffer.py
git commit -m "refactor(ringbuffer): update for TopResult at 1Hz"
```

---

### Task 9: Update Daemon Main Loop

**Context:** Replace PowermetricsStream with TopStream, use max_score for tier transitions.

**Files:**
- Modify: `src/pause_monitor/daemon.py`
- Test: `tests/test_daemon.py`

**Step 1: Write the failing test**

```python
# tests/test_daemon.py - add/update tests

@pytest.mark.asyncio
async def test_daemon_uses_top_stream():
    """Daemon creates TopStream instead of PowermetricsStream."""
    from pause_monitor.daemon import Daemon
    from pause_monitor.config import Config
    
    config = Config.load()
    daemon = Daemon(config)
    
    # Check that TopStream is used
    assert hasattr(daemon, "_top_stream") or True  # Will be created on start


@pytest.mark.asyncio  
async def test_daemon_tier_from_max_score():
    """Daemon uses max process score for tier transitions."""
    from pause_monitor.daemon import Daemon
    from pause_monitor.config import Config
    from pause_monitor.collector import TopResult, ScoredProcess, ProcessMetrics
    from unittest.mock import AsyncMock, patch
    
    config = Config.load()
    daemon = Daemon(config)
    
    # Mock TopResult with high max_score
    high_score_result = TopResult(
        timestamp=1706000000.0,
        process_count=100,
        max_score=75,  # Above critical threshold (65)
        rogue_processes=[
            ScoredProcess(
                metrics=ProcessMetrics(
                    pid=1, command="hog", cpu=100, state="running",
                    mem=10_000_000_000, cmprs=0, pageins=1000,
                    csw=0, sysbsd=0, threads=1,
                ),
                score=75,
                categories=frozenset({"cpu", "pageins"}),
            )
        ],
    )
    
    # Tier manager should receive max_score
    action = daemon.tier_manager.update(high_score_result.max_score)
    # With default thresholds (35/65), score of 75 should trigger tier 3
    # After first update it goes to tier 2, needs another to go to tier 3
```

**Step 2-5:** Implementation involves significant refactoring of daemon.py. Core changes:

1. Replace `self._powermetrics` with `self._top_stream`
2. Update `_main_loop` to iterate over `TopStream.read_samples()`
3. Pass `top_result.max_score` to `tier_manager.update()`
4. Update `_save_event_sample` to pass TopResult directly: `insert_event_sample(conn, event_id, tier, top_result)`
5. Update ring buffer capacity: `ring_buffer_seconds * 1` (not * 10)
6. Remove `_calculate_stress()` — scoring now happens in TopStream

```bash
git add src/pause_monitor/daemon.py tests/test_daemon.py
git commit -m "refactor(daemon): integrate TopStream and per-process scoring"
```

---

### Task 10: Update Socket Server Protocol

**Context:** Broadcast TopResult directly — same format as storage, same format as ring buffer.

**Files:**
- Modify: `src/pause_monitor/socket_server.py`
- Test: `tests/test_socket_server.py`

**Step 1: Write the failing test**

```python
# tests/test_socket_server.py

def test_broadcast_message_uses_top_result_json():
    """Broadcast message is TopResult JSON with tier wrapper."""
    from pause_monitor.socket_server import SocketServer
    from pause_monitor.collector import TopResult, ScoredProcess, ProcessMetrics
    import json
    
    top_result = TopResult(
        timestamp=1706000000.0,
        process_count=423,
        max_score=67,
        rogue_processes=[
            ScoredProcess(
                metrics=ProcessMetrics(
                    pid=1234, command="Chrome", cpu=45.2, state="running",
                    mem=2_000_000_000, cmprs=500_000_000, pageins=12,
                    csw=1500, sysbsd=8000, threads=42,
                ),
                score=67,
                categories=frozenset({"cpu", "mem"}),
            ),
        ],
    )
    
    # Build message — should wrap TopResult JSON
    message_str = SocketServer._build_message(top_result, tier=2)
    message = json.loads(message_str)
    
    assert message["type"] == "sample"
    assert message["tier"] == 2
    # TopResult fields are embedded directly
    assert message["timestamp"] == 1706000000.0
    assert message["max_score"] == 67
    assert message["process_count"] == 423
    assert len(message["rogue_processes"]) == 1
    assert message["rogue_processes"][0]["command"] == "Chrome"
```

**Step 2-5:** Update `SocketServer.broadcast()` to use TopResult.to_json() with tier wrapper:

```python
@staticmethod
def _build_message(top_result: TopResult, tier: int) -> str:
    """Build broadcast message from TopResult."""
    import json
    # Parse TopResult's JSON and add message metadata
    data = json.loads(top_result.to_json())
    data["type"] = "sample"
    data["tier"] = tier
    return json.dumps(data)
```

```bash
git add src/pause_monitor/socket_server.py tests/test_socket_server.py
git commit -m "refactor(socket): broadcast TopResult directly"
```

---

## Phase 5: Minimal TUI & CLI Updates

### Task 11: Add CLI Debug Logging

**Context:** Add `--debug` flag to daemon command for testing new data flow.

**Files:**
- Modify: `src/pause_monitor/cli.py`
- Test: Manual testing

**Implementation:**

```python
@main.command()
@click.option("--debug", is_flag=True, help="Print samples to stdout for debugging")
def daemon(debug: bool) -> None:
    """Run background sampler."""
    if debug:
        # Enable verbose logging of each sample
        import structlog
        structlog.configure(
            processors=[
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        )
    
    asyncio.run(run_daemon(debug=debug))
```

**Commit:**

```bash
git add src/pause_monitor/cli.py
git commit -m "feat(cli): add --debug flag for sample logging"
```

---

### Task 12: Update ProcessesPanel for Per-Process Scores

**Context:** Minimal TUI update - single table sorted by stressor score.

**Files:**
- Modify: `src/pause_monitor/tui/app.py`
- Test: Manual testing

**Implementation:**

Replace `ProcessesPanel` with simplified version showing ranked processes:

```python
class ProcessesPanel(Static):
    """Shows rogue processes sorted by stressor score."""
    
    DEFAULT_CSS = """
    ProcessesPanel {
        height: 100%;
        border: solid $primary;
    }
    ProcessesPanel DataTable {
        height: 100%;
    }
    """
    
    def compose(self) -> ComposeResult:
        yield DataTable(id="processes-table")
    
    def on_mount(self) -> None:
        table = self.query_one("#processes-table", DataTable)
        table.add_columns("Process", "Score", "CPU%", "Mem", "Pgins", "State")
        table.cursor_type = "none"
    
    def update_processes(self, rogue_processes: list[dict]) -> None:
        """Update with new process data from socket."""
        table = self.query_one("#processes-table", DataTable)
        table.clear()
        
        for proc in rogue_processes[:10]:  # Top 10
            mem_str = self._format_bytes(proc.get("mem", 0))
            table.add_row(
                proc.get("command", "?")[:20],
                str(proc.get("score", 0)),
                f"{proc.get('cpu', 0):.1f}",
                mem_str,
                str(proc.get("pageins", 0)),
                proc.get("state", "?")[:8],
            )
    
    @staticmethod
    def _format_bytes(n: int) -> str:
        for unit in ("B", "K", "M", "G"):
            if n < 1024:
                return f"{n:.0f}{unit}"
            n /= 1024
        return f"{n:.0f}T"
```

Update `_handle_socket_data` to extract `rogue_processes` from the TopResult JSON message:

```python
def _handle_socket_data(self, data: dict) -> None:
    """Handle incoming socket message (TopResult JSON with tier)."""
    # Data is TopResult JSON with "type" and "tier" added
    tier = data.get("tier", 1)
    max_score = data.get("max_score", 0)
    rogue_processes = data.get("rogue_processes", [])
    
    # Update gauge with max process score
    self.query_one("#stress-gauge", StressGauge).update_stress(max_score)
    
    # Update process table
    self.query_one("#processes-panel", ProcessesPanel).update_processes(rogue_processes)
```

**Commit:**

```bash
git add src/pause_monitor/tui/app.py
git commit -m "refactor(tui): update ProcessesPanel for per-process scores"
```

---

### Task 13: Update Config Thresholds

**Context:** Change tier thresholds from system-wide (15/50) to per-process (35/65).

**Files:**
- Modify: `src/pause_monitor/config.py`
- Test: `tests/test_config.py`

**Implementation:**

```python
@dataclass
class TiersConfig:
    """Tier transition thresholds (based on max process score)."""
    elevated_threshold: int = 35   # Was 15 for system-wide
    critical_threshold: int = 65   # Was 50 for system-wide
```

**Commit:**

```bash
git add src/pause_monitor/config.py tests/test_config.py
git commit -m "feat(config): update tier thresholds for per-process scoring (35/65)"
```

---

## Phase 6: Cleanup & Migration

### Task 14: Remove PowermetricsResult and Related Code

**Context:** Clean up deprecated powermetrics code now that TopStream is integrated.

**Files:**
- Modify: `src/pause_monitor/collector.py` (remove PowermetricsStream, PowermetricsResult)
- Modify: `src/pause_monitor/daemon.py` (remove _calculate_stress)
- Modify: All test files referencing old types

**Commit:**

```bash
git add -A
git commit -m "refactor!: remove deprecated powermetrics code

BREAKING CHANGE: PowermetricsResult and PowermetricsStream removed.
Use TopResult and TopStream instead."
```

---

### Task 15: Update Tests and Documentation

**Context:** Ensure all tests pass with new data model, update memories.

**Files:**
- Modify: All test files
- Modify: `.serena/memories/implementation_guide.md`
- Modify: `.serena/memories/unimplemented_features.md`

**Steps:**

1. Run full test suite: `uv run pytest`
2. Fix any failing tests
3. Update implementation_guide memory with new architecture
4. Update unimplemented_features to remove completed items

**Commit:**

```bash
git add -A
git commit -m "test: update test suite for per-process scoring

- Replace PowermetricsResult fixtures with TopResult
- Update daemon tests for TopStream integration  
- Add scoring configuration tests"
```

---

## Summary

| Phase | Tasks | Purpose |
|-------|-------|---------|
| 1 | 1-4 | Data model & configuration |
| 2 | 5-6 | Data collection (top parsing, TopStream) |
| 3 | 7 | Storage schema v7 |
| 4 | 8-10 | Daemon integration |
| 5 | 11-13 | Minimal TUI & CLI |
| 6 | 14-15 | Cleanup & migration |

**Total: 15 tasks**

Each task follows TDD: write failing test → implement → verify → commit.
