# Per-Process Stressor Scoring Implementation Plan

> **For Claude:** Use systema:executing-plans to implement this plan task-by-task.

**Goal:** Replace powermetrics with top-based per-process stressor scoring, using one canonical data format throughout the system.

**Architecture:**
- `top` at 1Hz provides per-process metrics (cpu, state, mem, cmprs, pageins, csw, sysbsd, threads)
- Rogue selection identifies 10-20 concerning processes per sample
- Each rogue gets a 0-100 stressor score based on 8 weighted metrics
- `ProcessSamples` flows through the entire system: collector → ring buffer → storage → socket → TUI
- Tier transitions driven by max process score (thresholds: 35/65)

**Key Decisions:**
- One canonical data format (`ProcessScore`, `ProcessSamples`)
- Top text parsing at 1Hz (hardcoded)
- JSON blob storage for event samples
- All weights and thresholds configurable
- Stuck processes always included (hardcoded)
- No backwards compatibility — clean break, delete old DB

**Patterns to Follow:**
- Dataclasses for data structures
- Config via TOML with nested sections
- Async iteration for sampling loop

**Tech Stack:** Python 3.14, top command, SQLite, Textual

---

## Task 1: Configuration Updates

**Context:** Add new config sections for scoring weights, rogue selection, and update tier thresholds.

**Files:**
- Modify: `src/pause_monitor/config.py`
- Test: `tests/test_config.py`

**Step 1: Write failing tests**

```python
# tests/test_config.py

def test_scoring_weights_default():
    """Scoring weights should have correct defaults."""
    config = Config()
    assert config.scoring.weights.cpu == 25
    assert config.scoring.weights.state == 20
    assert config.scoring.weights.pageins == 15
    assert config.scoring.weights.mem == 15
    assert config.scoring.weights.cmprs == 10
    assert config.scoring.weights.csw == 10
    assert config.scoring.weights.sysbsd == 5
    assert config.scoring.weights.threads == 0


def test_rogue_selection_default():
    """Rogue selection should have correct defaults."""
    config = Config()
    assert config.rogue_selection.cpu.enabled is True
    assert config.rogue_selection.cpu.count == 3
    assert config.rogue_selection.cpu.threshold == 0.0
    assert config.rogue_selection.state.enabled is True
    assert config.rogue_selection.state.states == ["stuck", "zombie"]


def test_tier_thresholds_updated():
    """Tier thresholds should be 35/65 for process scores."""
    config = Config()
    assert config.tiers.elevated_threshold == 35
    assert config.tiers.critical_threshold == 65
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_config.py -v -k "scoring or rogue or tier"
```

**Step 3: Implement configuration dataclasses**

```python
# src/pause_monitor/config.py

@dataclass
class ScoringWeights:
    """Weights for per-process stressor scoring (must sum to 100, excluding threads)."""
    cpu: int = 25
    state: int = 20
    pageins: int = 15
    mem: int = 15
    cmprs: int = 10
    csw: int = 10
    sysbsd: int = 5
    threads: int = 0


@dataclass
class ScoringConfig:
    """Scoring configuration."""
    weights: ScoringWeights = field(default_factory=ScoringWeights)


@dataclass
class CategorySelection:
    """Selection config for a single category."""
    enabled: bool = True
    count: int = 3
    threshold: float = 0.0


@dataclass
class StateSelection:
    """Selection config for state-based inclusion."""
    enabled: bool = True
    count: int = 0  # 0 = unlimited
    states: list[str] = field(default_factory=lambda: ["stuck", "zombie"])


@dataclass
class RogueSelectionConfig:
    """Configuration for rogue process selection."""
    cpu: CategorySelection = field(default_factory=CategorySelection)
    mem: CategorySelection = field(default_factory=CategorySelection)
    cmprs: CategorySelection = field(default_factory=CategorySelection)
    threads: CategorySelection = field(default_factory=CategorySelection)
    csw: CategorySelection = field(default_factory=CategorySelection)
    sysbsd: CategorySelection = field(default_factory=CategorySelection)
    pageins: CategorySelection = field(default_factory=CategorySelection)
    state: StateSelection = field(default_factory=StateSelection)


# Update TiersConfig defaults
@dataclass
class TiersConfig:
    """Tier thresholds for process scores."""
    elevated_threshold: int = 35  # Was 15
    critical_threshold: int = 65  # Was 50


# Update Config class
@dataclass
class Config:
    # ... existing fields ...
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    rogue_selection: RogueSelectionConfig = field(default_factory=RogueSelectionConfig)
```

**Step 4: Update TOML loading/saving for nested config**

**Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_config.py -v
```

**Step 6: Commit**

```bash
git add src/pause_monitor/config.py tests/test_config.py
git commit -m "feat(config): add scoring weights and rogue selection config"
```

---

## Task 2: Data Structures

**Context:** Define the canonical data format used throughout the system.

**Files:**
- Modify: `src/pause_monitor/collector.py`
- Test: `tests/test_collector.py`

**Step 1: Write failing tests**

```python
# tests/test_collector.py

def test_process_score_to_dict():
    """ProcessScore should serialize to dict."""
    ps = ProcessScore(
        pid=123,
        command="test",
        cpu=50.0,
        state="running",
        mem=1000000,
        cmprs=0,
        pageins=10,
        csw=100,
        sysbsd=50,
        threads=4,
        score=42,
        categories=frozenset({"cpu", "pageins"}),
    )
    d = ps.to_dict()
    assert d["pid"] == 123
    assert d["score"] == 42
    assert set(d["categories"]) == {"cpu", "pageins"}


def test_process_score_from_dict():
    """ProcessScore should deserialize from dict."""
    d = {
        "pid": 123,
        "command": "test",
        "cpu": 50.0,
        "state": "running",
        "mem": 1000000,
        "cmprs": 0,
        "pageins": 10,
        "csw": 100,
        "sysbsd": 50,
        "threads": 4,
        "score": 42,
        "categories": ["cpu", "pageins"],
    }
    ps = ProcessScore.from_dict(d)
    assert ps.pid == 123
    assert ps.categories == frozenset({"cpu", "pageins"})


def test_process_samples_json_roundtrip():
    """ProcessSamples should roundtrip through JSON."""
    samples = ProcessSamples(
        timestamp=datetime(2026, 1, 23, 12, 0, 0),
        elapsed_ms=1050,
        process_count=500,
        max_score=75,
        rogues=[
            ProcessScore(
                pid=1, command="test", cpu=80.0, state="running",
                mem=1000, cmprs=0, pageins=0, csw=10, sysbsd=5,
                threads=2, score=75, categories=frozenset({"cpu"}),
            ),
        ],
    )
    json_str = samples.to_json()
    restored = ProcessSamples.from_json(json_str)
    assert restored.max_score == 75
    assert len(restored.rogues) == 1
    assert restored.rogues[0].command == "test"
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_collector.py -v -k "process_score or process_samples"
```

**Step 3: Implement dataclasses**

```python
# src/pause_monitor/collector.py

import json
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ProcessScore:
    """Single process with its stressor score."""
    pid: int
    command: str
    cpu: float
    state: str
    mem: int
    cmprs: int
    pageins: int
    csw: int
    sysbsd: int
    threads: int
    score: int
    categories: frozenset[str]

    def to_dict(self) -> dict:
        return {
            "pid": self.pid,
            "command": self.command,
            "cpu": self.cpu,
            "state": self.state,
            "mem": self.mem,
            "cmprs": self.cmprs,
            "pageins": self.pageins,
            "csw": self.csw,
            "sysbsd": self.sysbsd,
            "threads": self.threads,
            "score": self.score,
            "categories": list(self.categories),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProcessScore":
        return cls(
            pid=data["pid"],
            command=data["command"],
            cpu=data["cpu"],
            state=data["state"],
            mem=data["mem"],
            cmprs=data["cmprs"],
            pageins=data["pageins"],
            csw=data["csw"],
            sysbsd=data["sysbsd"],
            threads=data["threads"],
            score=data["score"],
            categories=frozenset(data["categories"]),
        )


@dataclass
class ProcessSamples:
    """Collection of scored processes from one sample."""
    timestamp: datetime
    elapsed_ms: int
    process_count: int
    max_score: int
    rogues: list[ProcessScore]

    def to_json(self) -> str:
        return json.dumps({
            "timestamp": self.timestamp.isoformat(),
            "elapsed_ms": self.elapsed_ms,
            "process_count": self.process_count,
            "max_score": self.max_score,
            "rogues": [r.to_dict() for r in self.rogues],
        })

    @classmethod
    def from_json(cls, data: str) -> "ProcessSamples":
        d = json.loads(data)
        return cls(
            timestamp=datetime.fromisoformat(d["timestamp"]),
            elapsed_ms=d["elapsed_ms"],
            process_count=d["process_count"],
            max_score=d["max_score"],
            rogues=[ProcessScore.from_dict(r) for r in d["rogues"]],
        )
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_collector.py -v -k "process_score or process_samples"
```

**Step 5: Commit**

```bash
git add src/pause_monitor/collector.py tests/test_collector.py
git commit -m "feat(collector): add ProcessScore and ProcessSamples dataclasses"
```

---

## Task 3: Top Parsing

**Context:** Implement parsing of top command output.

**Files:**
- Modify: `src/pause_monitor/collector.py`
- Test: `tests/test_collector.py`

**Step 1: Write failing tests**

```python
# tests/test_collector.py

SAMPLE_TOP_OUTPUT = """
Processes: 500 total, 3 running, 497 sleeping, 4000 threads
2026/01/23 12:00:00
Load Avg: 2.00, 1.50, 1.00

PID    COMMAND          %CPU STATE    MEM    CMPRS  #TH    CSW        SYSBSD     PAGEINS
7229   chrome           47.1 running  339M   10M    38     1134810    3961273    68
409    WindowServer     27.7 running  1473M  0B     26     84562346   103373638  3427
0      kernel_task      18.1 stuck    43M    0B     870    793476910  0          0
620    zombie_proc      0.0  zombie   0B     0B     0      0          0          0
"""


def test_parse_top_output():
    """Should parse top output into process dicts."""
    from pause_monitor.collector import TopCollector
    
    collector = TopCollector(Config())
    processes = collector._parse_top_output(SAMPLE_TOP_OUTPUT)
    
    assert len(processes) == 4
    
    chrome = next(p for p in processes if p["command"] == "chrome")
    assert chrome["pid"] == 7229
    assert chrome["cpu"] == 47.1
    assert chrome["state"] == "running"
    assert chrome["mem"] == 339 * 1024 * 1024  # 339M in bytes
    assert chrome["pageins"] == 68


def test_parse_memory_suffixes():
    """Should handle M, K, G, B suffixes."""
    from pause_monitor.collector import TopCollector
    
    collector = TopCollector(Config())
    assert collector._parse_memory("339M") == 339 * 1024 * 1024
    assert collector._parse_memory("1473M") == 1473 * 1024 * 1024
    assert collector._parse_memory("43M") == 43 * 1024 * 1024
    assert collector._parse_memory("0B") == 0
    assert collector._parse_memory("1024K") == 1024 * 1024
    assert collector._parse_memory("2G") == 2 * 1024 * 1024 * 1024
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_collector.py -v -k "parse_top or parse_memory"
```

**Step 3: Implement parsing**

```python
# src/pause_monitor/collector.py

import re

class TopCollector:
    """Collects process data via top command at 1Hz."""
    
    def __init__(self, config: Config):
        self.config = config
    
    def _parse_memory(self, value: str) -> int:
        """Parse memory string like '339M', '1024K', '2G', '0B' to bytes."""
        value = value.strip().rstrip("+-")  # Remove +/- indicators
        if not value or value == "0":
            return 0
        
        match = re.match(r"(\d+(?:\.\d+)?)\s*([BKMG])?", value, re.IGNORECASE)
        if not match:
            return 0
        
        num = float(match.group(1))
        suffix = (match.group(2) or "B").upper()
        
        multipliers = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3}
        return int(num * multipliers.get(suffix, 1))
    
    def _parse_top_output(self, raw: str) -> list[dict]:
        """Parse top text output into raw process dicts."""
        lines = raw.strip().split("\n")
        processes = []
        
        # Find header line
        header_idx = None
        for i, line in enumerate(lines):
            if line.strip().startswith("PID"):
                header_idx = i
                break
        
        if header_idx is None:
            return []
        
        # Parse data lines after header
        for line in lines[header_idx + 1:]:
            parts = line.split()
            if len(parts) < 10:
                continue
            
            try:
                processes.append({
                    "pid": int(parts[0]),
                    "command": parts[1],
                    "cpu": float(parts[2]),
                    "state": parts[3],
                    "mem": self._parse_memory(parts[4]),
                    "cmprs": self._parse_memory(parts[5]),
                    "threads": int(parts[6].split("/")[0]),  # Handle "870/16" format
                    "csw": int(parts[7].rstrip("+")),
                    "sysbsd": int(parts[8].rstrip("+")),
                    "pageins": int(parts[9]),
                })
            except (ValueError, IndexError):
                continue
        
        return processes
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_collector.py -v -k "parse_top or parse_memory"
```

**Step 5: Commit**

```bash
git add src/pause_monitor/collector.py tests/test_collector.py
git commit -m "feat(collector): implement top output parsing"
```

---

## Task 4: Rogue Selection

**Context:** Implement selection of rogue processes based on config.

**Files:**
- Modify: `src/pause_monitor/collector.py`
- Test: `tests/test_collector.py`

**Step 1: Write failing tests**

```python
# tests/test_collector.py

def test_select_rogues_stuck_always_included():
    """Stuck processes should always be included."""
    config = Config()
    collector = TopCollector(config)
    
    processes = [
        {"pid": 1, "command": "normal", "cpu": 1.0, "state": "sleeping", "mem": 100, "cmprs": 0, "pageins": 0, "csw": 0, "sysbsd": 0, "threads": 1},
        {"pid": 2, "command": "stuck_proc", "cpu": 0.0, "state": "stuck", "mem": 100, "cmprs": 0, "pageins": 0, "csw": 0, "sysbsd": 0, "threads": 1},
    ]
    
    rogues = collector._select_rogues(processes)
    
    stuck = [r for r in rogues if r["command"] == "stuck_proc"]
    assert len(stuck) == 1
    assert "stuck" in stuck[0]["_categories"]


def test_select_rogues_top_n_per_category():
    """Should select top N per category."""
    config = Config()
    config.rogue_selection.cpu.count = 2
    collector = TopCollector(config)
    
    processes = [
        {"pid": 1, "command": "high_cpu", "cpu": 90.0, "state": "running", "mem": 100, "cmprs": 0, "pageins": 0, "csw": 0, "sysbsd": 0, "threads": 1},
        {"pid": 2, "command": "med_cpu", "cpu": 50.0, "state": "running", "mem": 100, "cmprs": 0, "pageins": 0, "csw": 0, "sysbsd": 0, "threads": 1},
        {"pid": 3, "command": "low_cpu", "cpu": 10.0, "state": "running", "mem": 100, "cmprs": 0, "pageins": 0, "csw": 0, "sysbsd": 0, "threads": 1},
    ]
    
    rogues = collector._select_rogues(processes)
    
    # Should have top 2 by CPU
    cpu_rogues = [r for r in rogues if "cpu" in r["_categories"]]
    commands = {r["command"] for r in cpu_rogues}
    assert "high_cpu" in commands
    assert "med_cpu" in commands
    assert "low_cpu" not in commands


def test_select_rogues_deduplicates():
    """Process in multiple categories should appear once."""
    config = Config()
    collector = TopCollector(config)
    
    processes = [
        {"pid": 1, "command": "multi", "cpu": 90.0, "state": "running", "mem": 1000000000, "cmprs": 0, "pageins": 100, "csw": 0, "sysbsd": 0, "threads": 1},
    ]
    
    rogues = collector._select_rogues(processes)
    
    assert len(rogues) == 1
    assert "cpu" in rogues[0]["_categories"]
    assert "mem" in rogues[0]["_categories"]
    assert "pageins" in rogues[0]["_categories"]
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_collector.py -v -k "select_rogues"
```

**Step 3: Implement rogue selection**

```python
# src/pause_monitor/collector.py

def _select_rogues(self, processes: list[dict]) -> list[dict]:
    """Apply rogue selection rules from config."""
    selected: dict[int, dict] = {}  # pid -> process with _categories
    
    # 1. Always include stuck (hardcoded)
    for proc in processes:
        if proc["state"] == "stuck":
            pid = proc["pid"]
            if pid not in selected:
                selected[pid] = {**proc, "_categories": set()}
            selected[pid]["_categories"].add("stuck")
    
    # 2. Include configured states (zombie, etc.)
    state_cfg = self.config.rogue_selection.state
    if state_cfg.enabled:
        matching = [p for p in processes if p["state"] in state_cfg.states and p["state"] != "stuck"]
        if state_cfg.count > 0:
            matching = matching[:state_cfg.count]
        for proc in matching:
            pid = proc["pid"]
            if pid not in selected:
                selected[pid] = {**proc, "_categories": set()}
            selected[pid]["_categories"].add("state")
    
    # 3. Top N per enabled category above threshold
    categories = [
        ("cpu", "cpu", self.config.rogue_selection.cpu),
        ("mem", "mem", self.config.rogue_selection.mem),
        ("cmprs", "cmprs", self.config.rogue_selection.cmprs),
        ("threads", "threads", self.config.rogue_selection.threads),
        ("csw", "csw", self.config.rogue_selection.csw),
        ("sysbsd", "sysbsd", self.config.rogue_selection.sysbsd),
        ("pageins", "pageins", self.config.rogue_selection.pageins),
    ]
    
    for cat_name, metric, cfg in categories:
        if not cfg.enabled:
            continue
        
        # Filter by threshold and sort
        eligible = [p for p in processes if p[metric] > cfg.threshold]
        eligible.sort(key=lambda p: p[metric], reverse=True)
        
        # Take top N
        for proc in eligible[:cfg.count]:
            pid = proc["pid"]
            if pid not in selected:
                selected[pid] = {**proc, "_categories": set()}
            selected[pid]["_categories"].add(cat_name)
    
    return list(selected.values())
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_collector.py -v -k "select_rogues"
```

**Step 5: Commit**

```bash
git add src/pause_monitor/collector.py tests/test_collector.py
git commit -m "feat(collector): implement rogue selection"
```

---

## Task 5: Process Scoring

**Context:** Compute stressor score for each rogue process.

**Files:**
- Modify: `src/pause_monitor/collector.py`
- Test: `tests/test_collector.py`

**Step 1: Write failing tests**

```python
# tests/test_collector.py

def test_score_process_cpu_heavy():
    """High CPU should result in high score."""
    config = Config()
    collector = TopCollector(config)
    
    proc = {
        "pid": 1, "command": "cpu_hog", "cpu": 100.0, "state": "running",
        "mem": 0, "cmprs": 0, "pageins": 0, "csw": 0, "sysbsd": 0, "threads": 1,
        "_categories": {"cpu"},
    }
    
    scored = collector._score_process(proc)
    
    assert scored.score >= 20  # CPU weight is 25, 100% should be near max


def test_score_process_stuck():
    """Stuck state should add significant score."""
    config = Config()
    collector = TopCollector(config)
    
    proc = {
        "pid": 1, "command": "stuck", "cpu": 0.0, "state": "stuck",
        "mem": 0, "cmprs": 0, "pageins": 0, "csw": 0, "sysbsd": 0, "threads": 1,
        "_categories": {"stuck"},
    }
    
    scored = collector._score_process(proc)
    
    assert scored.score >= 15  # State weight is 20, stuck should be high


def test_score_process_categories_preserved():
    """Categories should be preserved in ProcessScore."""
    config = Config()
    collector = TopCollector(config)
    
    proc = {
        "pid": 1, "command": "test", "cpu": 50.0, "state": "running",
        "mem": 1000000000, "cmprs": 0, "pageins": 0, "csw": 0, "sysbsd": 0, "threads": 1,
        "_categories": {"cpu", "mem"},
    }
    
    scored = collector._score_process(proc)
    
    assert scored.categories == frozenset({"cpu", "mem"})
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_collector.py -v -k "score_process"
```

**Step 3: Implement scoring**

```python
# src/pause_monitor/collector.py

def _score_process(self, proc: dict) -> ProcessScore:
    """Compute stressor score using config weights."""
    weights = self.config.scoring.weights
    
    # Normalize each metric to 0-1 scale
    normalized = {
        "cpu": min(1.0, proc["cpu"] / 100.0),
        "state": self._normalize_state(proc["state"]),
        "pageins": min(1.0, proc["pageins"] / 1000.0),
        "mem": min(1.0, proc["mem"] / (8 * 1024**3)),  # 8GB
        "cmprs": min(1.0, proc["cmprs"] / (1 * 1024**3)),  # 1GB
        "csw": min(1.0, proc["csw"] / 100000.0),  # 100k
        "sysbsd": min(1.0, proc["sysbsd"] / 100000.0),  # 100k
        "threads": min(1.0, proc["threads"] / 1000.0),  # 1000
    }
    
    # Weighted sum
    total = (
        normalized["cpu"] * weights.cpu +
        normalized["state"] * weights.state +
        normalized["pageins"] * weights.pageins +
        normalized["mem"] * weights.mem +
        normalized["cmprs"] * weights.cmprs +
        normalized["csw"] * weights.csw +
        normalized["sysbsd"] * weights.sysbsd +
        normalized["threads"] * weights.threads
    )
    
    score = min(100, int(total))
    
    return ProcessScore(
        pid=proc["pid"],
        command=proc["command"],
        cpu=proc["cpu"],
        state=proc["state"],
        mem=proc["mem"],
        cmprs=proc["cmprs"],
        pageins=proc["pageins"],
        csw=proc["csw"],
        sysbsd=proc["sysbsd"],
        threads=proc["threads"],
        score=score,
        categories=frozenset(proc["_categories"]),
    )


def _normalize_state(self, state: str) -> float:
    """Normalize state to 0-1 scale."""
    if state == "stuck":
        return 1.0
    elif state == "zombie":
        return 0.8
    elif state == "halted":
        return 0.6
    elif state == "stopped":
        return 0.4
    else:
        return 0.0
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_collector.py -v -k "score_process"
```

**Step 5: Commit**

```bash
git add src/pause_monitor/collector.py tests/test_collector.py
git commit -m "feat(collector): implement process scoring"
```

---

## Task 6: Top Collector Integration

**Context:** Implement the async collect() method that runs top and produces ProcessSamples.

**Files:**
- Modify: `src/pause_monitor/collector.py`
- Test: `tests/test_collector.py`

**Step 1: Write failing tests**

```python
# tests/test_collector.py

@pytest.mark.asyncio
async def test_collector_collect(mocker):
    """Collect should run top and return ProcessSamples."""
    config = Config()
    collector = TopCollector(config)
    
    # Mock _run_top to return sample output
    mocker.patch.object(collector, "_run_top", return_value=SAMPLE_TOP_OUTPUT)
    
    samples = await collector.collect()
    
    assert isinstance(samples, ProcessSamples)
    assert samples.process_count == 4
    assert samples.max_score > 0
    assert len(samples.rogues) > 0
    assert samples.elapsed_ms > 0
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_collector.py -v -k "collector_collect"
```

**Step 3: Implement collect()**

```python
# src/pause_monitor/collector.py

import asyncio
import time

class TopCollector:
    # ... existing methods ...
    
    async def collect(self) -> ProcessSamples:
        """Run top, parse output, select rogues, compute scores."""
        start = time.monotonic()
        
        raw = await self._run_top()
        all_processes = self._parse_top_output(raw)
        rogues = self._select_rogues(all_processes)
        scored = [self._score_process(p) for p in rogues]
        
        elapsed_ms = int((time.monotonic() - start) * 1000)
        max_score = max((p.score for p in scored), default=0)
        
        return ProcessSamples(
            timestamp=datetime.now(),
            elapsed_ms=elapsed_ms,
            process_count=len(all_processes),
            max_score=max_score,
            rogues=scored,
        )
    
    async def _run_top(self) -> str:
        """Run top command and return output."""
        cmd = [
            "top",
            "-l", "2",        # 2 samples (need delta)
            "-s", "1",        # 1 second interval
            "-n", "0",        # Unlimited processes
            "-stats", "pid,command,cpu,state,mem,cmprs,threads,csw,sysbsd,pageins",
        ]
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            raise RuntimeError(f"top failed: {stderr.decode()}")
        
        return stdout.decode()
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_collector.py -v -k "collector_collect"
```

**Step 5: Commit**

```bash
git add src/pause_monitor/collector.py tests/test_collector.py
git commit -m "feat(collector): implement TopCollector.collect()"
```

---

## Task 7: Storage Schema Update

**Context:** Update storage to v7 with JSON blob for event samples.

**Files:**
- Modify: `src/pause_monitor/storage.py`
- Test: `tests/test_storage.py`

**Step 1: Write failing tests**

```python
# tests/test_storage.py

def test_schema_version_7(initialized_db):
    """Schema version should be 7."""
    conn = get_connection(initialized_db)
    version = get_schema_version(conn)
    assert version == 7


def test_insert_event_sample_json(initialized_db):
    """Event sample should be stored as JSON."""
    conn = get_connection(initialized_db)
    
    event_id = create_event(conn, datetime.now())
    
    samples = ProcessSamples(
        timestamp=datetime.now(),
        elapsed_ms=1050,
        process_count=500,
        max_score=75,
        rogues=[
            ProcessScore(
                pid=1, command="test", cpu=80.0, state="running",
                mem=1000, cmprs=0, pageins=0, csw=10, sysbsd=5,
                threads=2, score=75, categories=frozenset({"cpu"}),
            ),
        ],
    )
    
    insert_event_sample(conn, event_id, tier=2, samples=samples)
    
    retrieved = get_event_samples(conn, event_id)
    assert len(retrieved) == 1
    assert retrieved[0].data.max_score == 75
    assert retrieved[0].data.rogues[0].command == "test"
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_storage.py -v -k "schema_version_7 or insert_event_sample_json"
```

**Step 3: Update schema and functions**

```python
# src/pause_monitor/storage.py

SCHEMA_VERSION = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_timestamp REAL NOT NULL,
    end_timestamp REAL,
    peak_score INTEGER,
    peak_tier INTEGER,
    status TEXT DEFAULT 'unreviewed',
    notes TEXT
);

CREATE TABLE IF NOT EXISTS event_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    tier INTEGER NOT NULL,
    data TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS daemon_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at REAL
);
"""


@dataclass
class EventSample:
    """Event sample with JSON data."""
    event_id: int
    tier: int
    data: ProcessSamples
    id: int | None = None


def insert_event_sample(conn: sqlite3.Connection, event_id: int, tier: int, samples: ProcessSamples) -> int:
    """Insert event sample as JSON blob."""
    cursor = conn.execute(
        "INSERT INTO event_samples (event_id, tier, data) VALUES (?, ?, ?)",
        (event_id, tier, samples.to_json()),
    )
    conn.commit()
    return cursor.lastrowid


def get_event_samples(conn: sqlite3.Connection, event_id: int) -> list[EventSample]:
    """Retrieve and deserialize event samples."""
    rows = conn.execute(
        "SELECT id, event_id, tier, data FROM event_samples WHERE event_id = ? ORDER BY id",
        (event_id,),
    ).fetchall()
    
    return [
        EventSample(
            id=row[0],
            event_id=row[1],
            tier=row[2],
            data=ProcessSamples.from_json(row[3]),
        )
        for row in rows
    ]
```

**Step 4: Remove legacy code (Sample, process_samples, etc.)**

**Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_storage.py -v
```

**Step 6: Commit**

```bash
git add src/pause_monitor/storage.py tests/test_storage.py
git commit -m "feat(storage): update to schema v7 with JSON blob storage"
```

---

## Task 8: Ring Buffer Update

**Context:** Update ring buffer to use ProcessSamples.

**Files:**
- Modify: `src/pause_monitor/ringbuffer.py`
- Test: `tests/test_ringbuffer.py`

**Step 1: Write failing tests**

```python
# tests/test_ringbuffer.py

def test_ring_buffer_push_process_samples():
    """Should accept ProcessSamples."""
    buffer = RingBuffer(max_samples=10)
    
    samples = ProcessSamples(
        timestamp=datetime.now(),
        elapsed_ms=1000,
        process_count=100,
        max_score=50,
        rogues=[],
    )
    
    buffer.push(samples, tier=1)
    
    assert len(buffer.samples) == 1
    assert buffer.samples[0].samples.max_score == 50


def test_ring_buffer_freeze():
    """Freeze should return immutable copy."""
    buffer = RingBuffer(max_samples=10)
    
    samples = ProcessSamples(
        timestamp=datetime.now(),
        elapsed_ms=1000,
        process_count=100,
        max_score=50,
        rogues=[],
    )
    
    buffer.push(samples, tier=1)
    contents = buffer.freeze()
    
    assert len(contents.samples) == 1
    buffer.push(samples, tier=2)  # Modify original
    assert len(contents.samples) == 1  # Frozen copy unchanged
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_ringbuffer.py -v
```

**Step 3: Update ring buffer**

```python
# src/pause_monitor/ringbuffer.py

from collections import deque
from dataclasses import dataclass
from datetime import datetime

from pause_monitor.collector import ProcessSamples


@dataclass
class RingSample:
    """Single sample in the ring buffer."""
    timestamp: datetime
    samples: ProcessSamples
    tier: int


@dataclass
class BufferContents:
    """Immutable snapshot for forensics."""
    samples: list[RingSample]


class RingBuffer:
    """Fixed-size circular buffer for process samples."""
    
    def __init__(self, max_samples: int = 30):
        self._samples: deque[RingSample] = deque(maxlen=max_samples)
    
    @property
    def samples(self) -> list[RingSample]:
        return list(self._samples)
    
    def push(self, samples: ProcessSamples, tier: int) -> None:
        """Add sample to buffer."""
        self._samples.append(RingSample(
            timestamp=datetime.now(),
            samples=samples,
            tier=tier,
        ))
    
    def freeze(self) -> BufferContents:
        """Return immutable copy for forensics."""
        return BufferContents(samples=list(self._samples))
```

**Step 4: Remove ProcessInfo, ProcessSnapshot, snapshot_processes(), clear_snapshots()**

**Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_ringbuffer.py -v
```

**Step 6: Commit**

```bash
git add src/pause_monitor/ringbuffer.py tests/test_ringbuffer.py
git commit -m "refactor(ringbuffer): update to use ProcessSamples"
```

---

## Task 9: TierManager Update

**Context:** Update defaults and rename stress to score.

**Files:**
- Modify: `src/pause_monitor/sentinel.py`
- Test: `tests/test_tier_manager.py`

**Step 1: Write failing tests**

```python
# tests/test_tier_manager.py

def test_tier_manager_default_thresholds():
    """Default thresholds should be 35/65."""
    tm = TierManager()
    assert tm._elevated_threshold == 35
    assert tm._critical_threshold == 65


def test_tier_manager_peak_score_property():
    """Should have peak_score property."""
    tm = TierManager()
    tm.update(40)  # Enter tier 2
    tm.update(50)  # Higher score
    
    assert tm.peak_score == 50
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_tier_manager.py -v -k "default_thresholds or peak_score"
```

**Step 3: Update TierManager**

```python
# src/pause_monitor/sentinel.py

class TierManager:
    """Manages tier transitions based on process scores."""
    
    def __init__(
        self,
        elevated_threshold: int = 35,
        critical_threshold: int = 65,
        deescalation_delay: float = 5.0,
    ):
        self._elevated_threshold = elevated_threshold
        self._critical_threshold = critical_threshold
        # ... rest unchanged ...
        self._peak_score = 0  # Was: _peak_stress
    
    @property
    def peak_score(self) -> int:  # Was: peak_stress
        return self._peak_score
    
    # Update docstrings to say "score" instead of "stress"
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_tier_manager.py -v
```

**Step 5: Commit**

```bash
git add src/pause_monitor/sentinel.py tests/test_tier_manager.py
git commit -m "refactor(sentinel): update thresholds and rename stress to score"
```

---

## Task 10: Daemon Update

**Context:** Integrate new collector and data flow.

**Files:**
- Modify: `src/pause_monitor/daemon.py`
- Test: `tests/test_daemon.py`

**Step 1: Write failing tests**

```python
# tests/test_daemon.py

@pytest.mark.asyncio
async def test_daemon_uses_top_collector(mocker, tmp_path):
    """Daemon should use TopCollector."""
    config = Config()
    config._data_dir = tmp_path
    
    daemon = Daemon(config)
    
    assert hasattr(daemon, "collector")
    assert isinstance(daemon.collector, TopCollector)


@pytest.mark.asyncio
async def test_daemon_main_loop_collects_samples(mocker, tmp_path):
    """Main loop should collect and process samples."""
    config = Config()
    config._data_dir = tmp_path
    
    daemon = Daemon(config)
    
    # Mock collector.collect()
    mock_samples = ProcessSamples(
        timestamp=datetime.now(),
        elapsed_ms=1000,
        process_count=100,
        max_score=40,
        rogues=[],
    )
    mocker.patch.object(daemon.collector, "collect", return_value=mock_samples)
    
    # Run one iteration
    daemon.state.running = True
    # ... test one loop iteration ...
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_daemon.py -v -k "top_collector or main_loop"
```

**Step 3: Update daemon**

```python
# src/pause_monitor/daemon.py

from pause_monitor.collector import TopCollector, ProcessSamples

class Daemon:
    def __init__(self, config: Config):
        self.config = config
        self.state = DaemonState()
        self.notifier = Notifier(config.alerts)
        
        self.collector = TopCollector(config)
        self.ring_buffer = RingBuffer(max_samples=config.sentinel.ring_buffer_seconds)
        self.tier_manager = TierManager(
            elevated_threshold=config.tiers.elevated_threshold,
            critical_threshold=config.tiers.critical_threshold,
        )
        # ...
    
    async def _main_loop(self) -> None:
        """Main 1Hz loop."""
        while self.state.running:
            try:
                samples = await self.collector.collect()
                
                tier = self.tier_manager.current_tier
                self.ring_buffer.push(samples, tier)
                
                action = self.tier_manager.update(samples.max_score)
                if action:
                    await self._handle_tier_action(action, samples)
                
                if tier >= 2:
                    self._save_event_sample(samples, tier)
                
                if self._socket_server.has_clients:
                    await self._socket_server.broadcast(samples, tier)
                
                expected_ms = 1000
                if samples.elapsed_ms > expected_ms * self.config.sentinel.pause_threshold_ratio:
                    await self._handle_pause(samples.elapsed_ms, expected_ms)
                
                self.state.update_sample(samples.max_score)
                
            except Exception as e:
                log.error("Sample failed", error=str(e))
                await asyncio.sleep(1)
```

**Step 4: Remove _calculate_stress() and PowermetricsStream references**

**Step 5: Update _handle_tier_action(), _save_event_sample(), _handle_pause()**

**Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_daemon.py -v
```

**Step 7: Commit**

```bash
git add src/pause_monitor/daemon.py tests/test_daemon.py
git commit -m "refactor(daemon): integrate TopCollector and new data flow"
```

---

## Task 11: Socket Server Update

**Context:** Update broadcast to use ProcessSamples.

**Files:**
- Modify: `src/pause_monitor/socket_server.py`
- Test: `tests/test_socket_server.py`

**Step 1: Write failing tests**

```python
# tests/test_socket_server.py

@pytest.mark.asyncio
async def test_broadcast_process_samples():
    """Broadcast should send ProcessSamples data."""
    # ... setup ...
    
    samples = ProcessSamples(
        timestamp=datetime.now(),
        elapsed_ms=1000,
        process_count=100,
        max_score=50,
        rogues=[
            ProcessScore(
                pid=1, command="test", cpu=50.0, state="running",
                mem=1000, cmprs=0, pageins=0, csw=0, sysbsd=0,
                threads=1, score=50, categories=frozenset({"cpu"}),
            ),
        ],
    )
    
    await server.broadcast(samples, tier=2)
    
    # Verify message format
    message = json.loads(received_data)
    assert message["type"] == "sample"
    assert message["max_score"] == 50
    assert len(message["rogues"]) == 1
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_socket_server.py -v
```

**Step 3: Update socket server**

```python
# src/pause_monitor/socket_server.py

async def broadcast(self, samples: ProcessSamples, tier: int) -> None:
    """Broadcast sample to all connected TUI clients."""
    if not self._clients:
        return
    
    message = {
        "type": "sample",
        "timestamp": samples.timestamp.isoformat(),
        "tier": tier,
        "elapsed_ms": samples.elapsed_ms,
        "process_count": samples.process_count,
        "max_score": samples.max_score,
        "rogues": [p.to_dict() for p in samples.rogues],
    }
    
    await self._send_to_all(json.dumps(message))
```

**Step 4: Update _send_initial_state()**

**Step 5: Remove old imports and parameters**

**Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_socket_server.py -v
```

**Step 7: Commit**

```bash
git add src/pause_monitor/socket_server.py tests/test_socket_server.py
git commit -m "refactor(socket): update broadcast for ProcessSamples"
```

---

## Task 12: TUI Update

**Context:** Minimal TUI updates to display new data.

**Files:**
- Modify: `src/pause_monitor/tui/app.py`
- Test: `tests/test_tui_connection.py`

**Step 1: Update StressGauge**

```python
# Rename update_stress to update_score
def update_score(self, score: int) -> None:
    """Update gauge with max process score."""
    # ... same logic, different name ...
```

**Step 2: Update ProcessesPanel**

```python
def update_rogues(self, rogues: list[dict]) -> None:
    """Update with rogue process list."""
    table = self.query_one(DataTable)
    table.clear()
    
    for p in rogues[:10]:  # Show top 10
        table.add_row(
            p["command"][:20],
            str(p["score"]),
            f"{p['cpu']:.1f}%",
            self._format_bytes(p["mem"]),
            str(p["pageins"]),
            p["state"][:8],
        )
```

**Step 3: Update _handle_socket_data()**

```python
def _handle_socket_data(self, data: dict) -> None:
    if data["type"] == "sample":
        self.query_one(StressGauge).update_score(data["max_score"])
        self.query_one(ProcessesPanel).update_rogues(data["rogues"])
        # ... tier update ...
```

**Step 4: Simplify/remove MetricsPanel (no power/thermal data)**

**Step 5: Update CSS for new layout**

**Step 6: Run tests**

```bash
uv run pytest tests/test_tui_connection.py -v
```

**Step 7: Commit**

```bash
git add src/pause_monitor/tui/app.py tests/test_tui_connection.py
git commit -m "refactor(tui): update for ProcessSamples display"
```

---

## Task 13: Forensics Update

**Context:** Update forensics to use new data format.

**Files:**
- Modify: `src/pause_monitor/forensics.py`
- Test: `tests/test_forensics.py`

**Step 1: Update write_ring_buffer()**

```python
def write_ring_buffer(capture: ForensicsCapture, contents: BufferContents) -> None:
    data = {
        "samples": [
            {
                "timestamp": s.timestamp.isoformat(),
                "tier": s.tier,
                "max_score": s.samples.max_score,
                "process_count": s.samples.process_count,
                "rogues": [p.to_dict() for p in s.samples.rogues],
            }
            for s in contents.samples
        ]
    }
    capture.write_text_artifact("ring_buffer.json", json.dumps(data, indent=2))
```

**Step 2: Simplify identify_culprits()**

**Step 3: Remove PowermetricsResult references**

**Step 4: Run tests**

```bash
uv run pytest tests/test_forensics.py -v
```

**Step 5: Commit**

```bash
git add src/pause_monitor/forensics.py tests/test_forensics.py
git commit -m "refactor(forensics): update for ProcessSamples"
```

---

## Task 14: CLI Update

**Context:** Update CLI commands to display new data format.

**Files:**
- Modify: `src/pause_monitor/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Update events_show()**

```python
for sample in samples:
    console.print(f"  Tier {sample.tier} | Max Score: {sample.data.max_score}")
    for rogue in sample.data.rogues[:5]:
        console.print(f"    {rogue.command}: {rogue.score}")
```

**Step 2: Update status() to show max_score**

**Step 3: Remove stress breakdown display**

**Step 4: Run tests**

```bash
uv run pytest tests/test_cli.py -v
```

**Step 5: Commit**

```bash
git add src/pause_monitor/cli.py tests/test_cli.py
git commit -m "refactor(cli): update for ProcessSamples display"
```

---

## Task 15: Cleanup

**Context:** Remove all obsolete code.

**Files:**
- Delete: `src/pause_monitor/stress.py`
- Modify: Multiple files to remove dead imports

**Step 1: Delete stress.py**

```bash
rm src/pause_monitor/stress.py
rm tests/test_stress.py
```

**Step 2: Remove from collector.py**

- Delete `PowermetricsStream` class
- Delete `PowermetricsResult` dataclass
- Delete `parse_powermetrics_sample()` function
- Keep `get_core_count()` (might be useful)

**Step 3: Remove legacy storage code**

- Delete `Sample` dataclass
- Delete `insert_sample()`, `get_recent_samples()`
- Remove legacy table references

**Step 4: Update all imports**

```bash
# Find and fix all broken imports
uv run ruff check . --fix
```

**Step 5: Run full test suite**

```bash
uv run pytest
```

**Step 6: Run linter**

```bash
uv run ruff check . && uv run ruff format .
```

**Step 7: Commit**

```bash
git add -A
git commit -m "chore: remove obsolete powermetrics and stress code"
```

---

## Task 16: Integration Test

**Context:** Verify end-to-end functionality.

**Step 1: Manual testing**

```bash
# Delete old database
rm -rf ~/.local/share/pause-monitor/

# Run daemon
uv run pause-monitor daemon

# In another terminal, run TUI
uv run pause-monitor tui

# Verify:
# - Processes appear with scores
# - Tier transitions work
# - Events are captured
```

**Step 2: Create integration test**

```python
# tests/test_integration.py

@pytest.mark.asyncio
async def test_full_collection_cycle():
    """Test complete collection → storage → display cycle."""
    # ... comprehensive integration test ...
```

**Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration tests for per-process scoring"
```

---

## Summary

| Task | Description |
|------|-------------|
| 1 | Configuration updates |
| 2 | Data structures (ProcessScore, ProcessSamples) |
| 3 | Top parsing |
| 4 | Rogue selection |
| 5 | Process scoring |
| 6 | Top collector integration |
| 7 | Storage schema update (v7) |
| 8 | Ring buffer update |
| 9 | TierManager update |
| 10 | Daemon update |
| 11 | Socket server update |
| 12 | TUI update |
| 13 | Forensics update |
| 14 | CLI update |
| 15 | Cleanup |
| 16 | Integration test |

**Execution order:** Tasks are ordered for TDD — each builds on the previous. Complete them sequentially.
