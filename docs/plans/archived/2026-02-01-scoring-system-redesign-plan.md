# Scoring System Redesign Implementation Plan

> **For Claude:** Implement this plan task-by-task, following the TDD workflow in each task.

**Goal:** Replace the 4-category scoring system with a disproportionate-share model using Apple-style resource weighting, producing scores that spread across all bands with graduated capture behaviors.

**Architecture:** The new scoring model calculates each process's share of total system resources compared to "fair share" (1 ÷ active process count). Resources are weighted Apple-style (GPU > CPU, wakeups penalized). A logarithmic curve maps disproportionality to scores, making high band reachable under load while critical remains rare. Capture frequency graduates by band: low (none), medium (every N samples), elevated (every M samples), high (every sample), critical (every sample + forensics).

**Key Decisions:**
- Active process = non-idle state AND using measurable resources (CPU > 0 OR memory > threshold OR disk I/O > 0)
- Logarithmic score curve — high band at ~50-100× fair share, critical at ~200×+
- Dominant reporting switches from categories (blocking/contention/pressure/efficiency) to resources (CPU/GPU/memory/disk/wakeups)
- All weights fully configurable in config.toml — no hardcoding
- Dead metrics kept with zero weight default — can analyze and prune later
- Schema increments to v18 — clean slate, old scores incomparable

**Graduated Band Behaviors:**
| Band | Score | Persistence |
|------|-------|-------------|
| Low | 0-19 | None (ring buffer only) |
| Medium | 20-39 | Every N samples (configurable, default 20) |
| Elevated | 40-49 | Every M samples (configurable, default 10) |
| High | 50-69 | Every sample |
| Critical | 70-100 | Every sample + full forensics |

**Patterns to Follow:**
- Dataclass-based configuration with defaults
- Delta-based rate calculation for metrics
- TDD for all new code
- No stubs — implement fully or don't write it

**Tech Stack:** Python 3.14, SQLite with WAL, libproc/IOKit for metrics collection, pytest for testing

---

## Task 1: Add Resource Weights Configuration

**Context:** The new scoring model needs configurable weights for each resource type. Currently weights are hardcoded in `collector.py`. This task creates the configuration foundation.

**Files:**
- Modify: `src/rogue_hunter/config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

```python
# tests/test_config.py

def test_resource_weights_defaults():
    """Resource weights have sensible Apple-style defaults."""
    from rogue_hunter.config import ResourceWeights

    weights = ResourceWeights()

    # GPU weighted higher than CPU (Apple model)
    assert weights.gpu > weights.cpu
    # Wakeups penalized
    assert weights.wakeups > 0
    # All weights are positive
    assert all(w > 0 for w in [weights.cpu, weights.gpu, weights.memory, weights.disk_io, weights.wakeups])


def test_resource_weights_in_scoring_config():
    """ResourceWeights accessible via ScoringConfig."""
    from rogue_hunter.config import ScoringConfig

    scoring = ScoringConfig()

    assert hasattr(scoring, 'resource_weights')
    assert scoring.resource_weights.cpu > 0


def test_active_process_thresholds_defaults():
    """Active process thresholds have defaults."""
    from rogue_hunter.config import ScoringConfig

    scoring = ScoringConfig()

    assert hasattr(scoring, 'active_min_cpu')
    assert hasattr(scoring, 'active_min_memory_mb')
    assert hasattr(scoring, 'active_min_disk_io')
    # Defaults should be small but non-zero
    assert scoring.active_min_cpu >= 0
    assert scoring.active_min_memory_mb >= 0
    assert scoring.active_min_disk_io >= 0


def test_config_load_resource_weights():
    """Resource weights load from TOML."""
    from rogue_hunter.config import Config
    import tempfile
    from pathlib import Path

    toml_content = """
[scoring.resource_weights]
cpu = 1.5
gpu = 4.0
memory = 1.0
disk_io = 1.0
wakeups = 2.0
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
        f.write(toml_content)
        f.flush()

        config = Config.load(Path(f.name))

        assert config.scoring.resource_weights.cpu == 1.5
        assert config.scoring.resource_weights.gpu == 4.0
```

**Step 2: Run test to verify it fails**

```bash
timeout 10s uv run pytest tests/test_config.py::test_resource_weights_defaults -v
```
Expected: FAIL with `ImportError` or `AttributeError` (ResourceWeights doesn't exist)

**Step 3: Write minimal implementation**

```python
# src/rogue_hunter/config.py

# Add after StateMultipliers dataclass:

@dataclass
class ResourceWeights:
    """Weights for each resource type in scoring (Apple-style model).

    Higher weight = more impact on score.
    GPU weighted higher because GPU work is intensive.
    Wakeups penalized because they cause system-wide disruption.
    """
    cpu: float = 1.0
    gpu: float = 3.0  # GPU work is intensive
    memory: float = 1.0
    disk_io: float = 1.0
    wakeups: float = 2.0  # Penalized for system disruption


# Modify ScoringConfig dataclass:

@dataclass
class ScoringConfig:
    """Scoring configuration.

    Contains resource weights, normalization thresholds, state multipliers,
    and active process detection thresholds.
    """
    resource_weights: ResourceWeights = field(default_factory=ResourceWeights)
    state_multipliers: StateMultipliers = field(default_factory=StateMultipliers)
    normalization: NormalizationConfig = field(default_factory=NormalizationConfig)
    # Thresholds for counting a process as "active" for fair share calculation
    active_min_cpu: float = 0.1  # Minimum CPU % to be considered active
    active_min_memory_mb: float = 10.0  # Minimum memory MB to be considered active
    active_min_disk_io: float = 0.0  # Minimum disk I/O bytes/sec (0 = any disk activity counts)


# Update _load_scoring_config() to load resource_weights:

def _load_scoring_config(data: dict) -> ScoringConfig:
    """Load scoring configuration from TOML data."""
    scoring_data = data.get("scoring", {})
    defaults = ScoringConfig()

    # Load resource weights
    weights_data = scoring_data.get("resource_weights", {})
    resource_weights = ResourceWeights(
        cpu=weights_data.get("cpu", defaults.resource_weights.cpu),
        gpu=weights_data.get("gpu", defaults.resource_weights.gpu),
        memory=weights_data.get("memory", defaults.resource_weights.memory),
        disk_io=weights_data.get("disk_io", defaults.resource_weights.disk_io),
        wakeups=weights_data.get("wakeups", defaults.resource_weights.wakeups),
    )

    # Load state multipliers (existing code)
    mult_data = scoring_data.get("state_multipliers", {})
    state_multipliers = StateMultipliers(
        idle=mult_data.get("idle", defaults.state_multipliers.idle),
        sleeping=mult_data.get("sleeping", defaults.state_multipliers.sleeping),
        stopped=mult_data.get("stopped", defaults.state_multipliers.stopped),
        zombie=mult_data.get("zombie", defaults.state_multipliers.zombie),
        running=mult_data.get("running", defaults.state_multipliers.running),
        stuck=mult_data.get("stuck", defaults.state_multipliers.stuck),
    )

    # Load normalization (existing code)
    norm_data = scoring_data.get("normalization", {})
    normalization = NormalizationConfig(
        cpu=norm_data.get("cpu", defaults.normalization.cpu),
        mem_gb=norm_data.get("mem_gb", defaults.normalization.mem_gb),
        pageins_rate=norm_data.get("pageins_rate", defaults.normalization.pageins_rate),
        faults_rate=norm_data.get("faults_rate", defaults.normalization.faults_rate),
        disk_io_rate=norm_data.get("disk_io_rate", defaults.normalization.disk_io_rate),
        csw_rate=norm_data.get("csw_rate", defaults.normalization.csw_rate),
        syscalls_rate=norm_data.get("syscalls_rate", defaults.normalization.syscalls_rate),
        mach_msgs_rate=norm_data.get("mach_msgs_rate", defaults.normalization.mach_msgs_rate),
        wakeups_rate=norm_data.get("wakeups_rate", defaults.normalization.wakeups_rate),
        threads=norm_data.get("threads", defaults.normalization.threads),
        runnable_time_rate=norm_data.get("runnable_time_rate", defaults.normalization.runnable_time_rate),
        qos_interactive_rate=norm_data.get("qos_interactive_rate", defaults.normalization.qos_interactive_rate),
        gpu_time_rate=norm_data.get("gpu_time_rate", defaults.normalization.gpu_time_rate),
        ipc_min=norm_data.get("ipc_min", defaults.normalization.ipc_min),
    )

    # Load active process thresholds
    active_min_cpu = scoring_data.get("active_min_cpu", defaults.active_min_cpu)
    active_min_memory_mb = scoring_data.get("active_min_memory_mb", defaults.active_min_memory_mb)
    active_min_disk_io = scoring_data.get("active_min_disk_io", defaults.active_min_disk_io)

    return ScoringConfig(
        resource_weights=resource_weights,
        state_multipliers=state_multipliers,
        normalization=normalization,
        active_min_cpu=active_min_cpu,
        active_min_memory_mb=active_min_memory_mb,
        active_min_disk_io=active_min_disk_io,
    )
```

**Step 4: Run test to verify it passes**

```bash
timeout 10s uv run pytest tests/test_config.py::test_resource_weights_defaults tests/test_config.py::test_resource_weights_in_scoring_config tests/test_config.py::test_active_process_thresholds_defaults tests/test_config.py::test_config_load_resource_weights -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/rogue_hunter/config.py tests/test_config.py
git commit -m "$(cat <<'EOF'
feat: Add resource weights and active process thresholds to config

Scoring system needs configurable weights for Apple-style resource
weighting (GPU > CPU, wakeups penalized) and thresholds for determining
which processes count as "active" for fair share calculation.

Changes:
- Add ResourceWeights dataclass with cpu/gpu/memory/disk_io/wakeups
- Add active_min_cpu/memory_mb/disk_io to ScoringConfig
- Update _load_scoring_config() to load new fields from TOML
EOF
)"
```

---

## Task 2: Add Sample-Based Checkpoint Configuration

**Context:** Graduated capture requires different checkpoint frequencies per band. Currently there's a single time-based `checkpoint_interval`. This task adds sample-based checkpoint configuration.

**Files:**
- Modify: `src/rogue_hunter/config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

```python
# tests/test_config.py

def test_checkpoint_samples_defaults():
    """Checkpoint sample counts have defaults for each band."""
    from rogue_hunter.config import BandsConfig

    bands = BandsConfig()

    # Medium has less frequent checkpoints than elevated
    assert hasattr(bands, 'medium_checkpoint_samples')
    assert hasattr(bands, 'elevated_checkpoint_samples')
    assert bands.medium_checkpoint_samples > bands.elevated_checkpoint_samples
    # Both are positive integers
    assert bands.medium_checkpoint_samples > 0
    assert bands.elevated_checkpoint_samples > 0


def test_checkpoint_samples_configurable():
    """Checkpoint sample counts load from TOML."""
    from rogue_hunter.config import Config
    import tempfile
    from pathlib import Path

    toml_content = """
[bands]
medium_checkpoint_samples = 30
elevated_checkpoint_samples = 15
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
        f.write(toml_content)
        f.flush()

        config = Config.load(Path(f.name))

        assert config.bands.medium_checkpoint_samples == 30
        assert config.bands.elevated_checkpoint_samples == 15
```

**Step 2: Run test to verify it fails**

```bash
timeout 10s uv run pytest tests/test_config.py::test_checkpoint_samples_defaults -v
```
Expected: FAIL with `AttributeError` (medium_checkpoint_samples doesn't exist)

**Step 3: Write minimal implementation**

```python
# src/rogue_hunter/config.py

# Modify BandsConfig dataclass:

@dataclass
class BandsConfig:
    """Score band thresholds and capture behavior configuration.

    Band behaviors:
    - Low (0 to medium-1): No persistence, ring buffer only
    - Medium (medium to elevated-1): Checkpoint every medium_checkpoint_samples
    - Elevated (elevated to high-1): Checkpoint every elevated_checkpoint_samples
    - High (high to critical-1): Every sample persisted
    - Critical (critical to 100): Every sample + full forensics
    """
    medium: int = 20
    elevated: int = 40
    high: int = 50
    critical: int = 70
    tracking_band: str = "medium"  # Changed: tracking now starts at medium
    forensics_band: str = "critical"  # Only critical triggers forensics
    checkpoint_interval: int = 30  # Deprecated: kept for backwards compatibility
    # Sample-based checkpoint intervals (new)
    medium_checkpoint_samples: int = 20  # ~66s at 3 samples/sec
    elevated_checkpoint_samples: int = 10  # ~33s at 3 samples/sec

    # ... rest of existing methods unchanged


# Update _load_bands_config() to load new fields:

def _load_bands_config(data: dict) -> BandsConfig:
    """Load bands configuration from TOML data."""
    bands_data = data.get("bands", {})
    defaults = BandsConfig()

    # Load thresholds
    medium = bands_data.get("medium", defaults.medium)
    elevated = bands_data.get("elevated", defaults.elevated)
    high = bands_data.get("high", defaults.high)
    critical = bands_data.get("critical", defaults.critical)

    # Load band names
    tracking_band = bands_data.get("tracking_band", defaults.tracking_band)
    forensics_band = bands_data.get("forensics_band", defaults.forensics_band)

    # Validate band names
    valid_bands = {"low", "medium", "elevated", "high", "critical"}
    if tracking_band not in valid_bands:
        raise ValueError(f"Invalid tracking_band: {tracking_band}")
    if forensics_band not in valid_bands:
        raise ValueError(f"Invalid forensics_band: {forensics_band}")

    # Load intervals
    checkpoint_interval = bands_data.get("checkpoint_interval", defaults.checkpoint_interval)
    medium_checkpoint_samples = bands_data.get("medium_checkpoint_samples", defaults.medium_checkpoint_samples)
    elevated_checkpoint_samples = bands_data.get("elevated_checkpoint_samples", defaults.elevated_checkpoint_samples)

    return BandsConfig(
        medium=medium,
        elevated=elevated,
        high=high,
        critical=critical,
        tracking_band=tracking_band,
        forensics_band=forensics_band,
        checkpoint_interval=checkpoint_interval,
        medium_checkpoint_samples=medium_checkpoint_samples,
        elevated_checkpoint_samples=elevated_checkpoint_samples,
    )
```

**Step 4: Run test to verify it passes**

```bash
timeout 10s uv run pytest tests/test_config.py::test_checkpoint_samples_defaults tests/test_config.py::test_checkpoint_samples_configurable -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/rogue_hunter/config.py tests/test_config.py
git commit -m "$(cat <<'EOF'
feat: Add sample-based checkpoint configuration for graduated capture

Graduated band behaviors require different checkpoint frequencies:
- Medium: less frequent (every 20 samples)
- Elevated: more frequent (every 10 samples)
- High/Critical: every sample (no config needed)

Changes:
- Add medium_checkpoint_samples and elevated_checkpoint_samples to BandsConfig
- Update _load_bands_config() to load new fields
- Change tracking_band default to "medium" (tracking now starts earlier)
- Change forensics_band default to "critical" (forensics only at critical)
EOF
)"
```

---

## Task 3: Update ProcessScore Dataclass

**Context:** The scoring model changes from 4 categories to resource-based. ProcessScore needs new fields for resource shares and dominant resource, replacing the old category scores.

**Files:**
- Modify: `src/rogue_hunter/collector.py`
- Test: `tests/test_collector.py`

**Step 1: Write the failing test**

```python
# tests/test_collector.py

def test_process_score_has_resource_shares():
    """ProcessScore has resource share fields instead of category scores."""
    from rogue_hunter.collector import ProcessScore
    from datetime import datetime

    # Create a ProcessScore with the new fields
    score = ProcessScore(
        pid=123,
        command="test",
        captured_at=datetime.now().timestamp(),
        # Resource shares (new)
        cpu_share=2.5,
        gpu_share=0.0,
        mem_share=1.2,
        disk_share=0.5,
        wakeups_share=0.1,
        disproportionality=2.5,  # Highest share
        dominant_resource="cpu",
        # Raw metrics (unchanged)
        cpu=25.0,
        mem=1024000,
        mem_peak=2048000,
        pageins=0,
        pageins_rate=0.0,
        faults=100,
        faults_rate=10.0,
        disk_io=50000,
        disk_io_rate=5000.0,
        csw=1000,
        csw_rate=100.0,
        syscalls=5000,
        syscalls_rate=500.0,
        threads=4,
        mach_msgs=100,
        mach_msgs_rate=10.0,
        instructions=1000000,
        cycles=2000000,
        ipc=0.5,
        energy=1000,
        energy_rate=100.0,
        wakeups=10,
        wakeups_rate=1.0,
        runnable_time=5000,
        runnable_time_rate=0.5,
        qos_interactive=0,
        qos_interactive_rate=0.0,
        gpu_time=0,
        gpu_time_rate=0.0,
        zombie_children=0,
        state="running",
        priority=31,
        score=45,
        band="elevated",
    )

    assert score.cpu_share == 2.5
    assert score.dominant_resource == "cpu"
    assert score.disproportionality == 2.5


def test_process_score_no_category_scores():
    """ProcessScore no longer has category score fields."""
    from rogue_hunter.collector import ProcessScore

    # These fields should not exist
    assert not hasattr(ProcessScore, 'blocking_score') or 'blocking_score' not in ProcessScore.__dataclass_fields__
    assert not hasattr(ProcessScore, 'contention_score') or 'contention_score' not in ProcessScore.__dataclass_fields__
    assert not hasattr(ProcessScore, 'pressure_score') or 'pressure_score' not in ProcessScore.__dataclass_fields__
    assert not hasattr(ProcessScore, 'efficiency_score') or 'efficiency_score' not in ProcessScore.__dataclass_fields__
    assert not hasattr(ProcessScore, 'dominant_category') or 'dominant_category' not in ProcessScore.__dataclass_fields__
    assert not hasattr(ProcessScore, 'dominant_metrics') or 'dominant_metrics' not in ProcessScore.__dataclass_fields__
```

**Step 2: Run test to verify it fails**

```bash
timeout 10s uv run pytest tests/test_collector.py::test_process_score_has_resource_shares -v
```
Expected: FAIL with `TypeError` (unexpected keyword arguments for new fields)

**Step 3: Write minimal implementation**

```python
# src/rogue_hunter/collector.py

# Replace the ProcessScore dataclass with:

@dataclass
class ProcessScore:
    """Canonical schema for scored process data.

    DO NOT create alternative representations. All code that handles process
    data should use this schema directly.

    Resource shares represent how many multiples of "fair share" this process
    consumes for each resource type. A share of 1.0 means exactly fair share;
    10.0 means 10x fair share.
    """

    # Identity
    pid: int
    command: str
    captured_at: float  # Unix timestamp

    # Resource shares (multiples of fair share)
    cpu_share: float
    gpu_share: float
    mem_share: float
    disk_share: float
    wakeups_share: float
    disproportionality: float  # Highest share value (used for dominant)
    dominant_resource: str  # "cpu", "gpu", "memory", "disk", or "wakeups"

    # Raw metrics - CPU
    cpu: float  # CPU usage percentage (can exceed 100% on multi-core)

    # Raw metrics - Memory
    mem: int  # Current physical memory (bytes)
    mem_peak: int  # Lifetime peak memory (bytes)
    pageins: int  # Total page-ins (cumulative)
    pageins_rate: float  # Page-ins per second

    # Raw metrics - Faults
    faults: int  # Total page faults (cumulative)
    faults_rate: float  # Faults per second

    # Raw metrics - Disk I/O
    disk_io: int  # Total disk I/O bytes (cumulative)
    disk_io_rate: float  # Disk I/O bytes per second

    # Raw metrics - Context switches
    csw: int  # Total context switches (cumulative)
    csw_rate: float  # Context switches per second

    # Raw metrics - Syscalls
    syscalls: int  # Total syscalls (cumulative)
    syscalls_rate: float  # Syscalls per second

    # Raw metrics - Threads
    threads: int  # Current thread count

    # Raw metrics - Mach messages
    mach_msgs: int  # Total Mach messages (cumulative)
    mach_msgs_rate: float  # Mach messages per second

    # Raw metrics - CPU efficiency
    instructions: int  # CPU instructions (cumulative)
    cycles: int  # CPU cycles (cumulative)
    ipc: float  # Instructions per cycle

    # Raw metrics - Energy
    energy: int  # Billed energy (cumulative)
    energy_rate: float  # Energy per second

    # Raw metrics - Wakeups
    wakeups: int  # Total wakeups (cumulative)
    wakeups_rate: float  # Wakeups per second

    # Raw metrics - Scheduling
    runnable_time: int  # Time spent runnable (mach units, cumulative)
    runnable_time_rate: float  # Runnable time rate (ms/sec)
    qos_interactive: int  # QoS interactive time (mach units, cumulative)
    qos_interactive_rate: float  # QoS interactive rate (ms/sec)

    # Raw metrics - GPU
    gpu_time: int  # GPU time (nanoseconds, cumulative)
    gpu_time_rate: float  # GPU time rate (ms/sec)

    # Raw metrics - Other
    zombie_children: int  # Count of zombie child processes
    state: str  # Process state (running, sleeping, etc.)
    priority: int  # Scheduler priority

    # Final score
    score: int  # 0-100 composite score
    band: str  # low, medium, elevated, high, critical

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "pid": self.pid,
            "command": self.command,
            "captured_at": self.captured_at,
            "cpu_share": self.cpu_share,
            "gpu_share": self.gpu_share,
            "mem_share": self.mem_share,
            "disk_share": self.disk_share,
            "wakeups_share": self.wakeups_share,
            "disproportionality": self.disproportionality,
            "dominant_resource": self.dominant_resource,
            "cpu": self.cpu,
            "mem": self.mem,
            "mem_peak": self.mem_peak,
            "pageins": self.pageins,
            "pageins_rate": self.pageins_rate,
            "faults": self.faults,
            "faults_rate": self.faults_rate,
            "disk_io": self.disk_io,
            "disk_io_rate": self.disk_io_rate,
            "csw": self.csw,
            "csw_rate": self.csw_rate,
            "syscalls": self.syscalls,
            "syscalls_rate": self.syscalls_rate,
            "threads": self.threads,
            "mach_msgs": self.mach_msgs,
            "mach_msgs_rate": self.mach_msgs_rate,
            "instructions": self.instructions,
            "cycles": self.cycles,
            "ipc": self.ipc,
            "energy": self.energy,
            "energy_rate": self.energy_rate,
            "wakeups": self.wakeups,
            "wakeups_rate": self.wakeups_rate,
            "runnable_time": self.runnable_time,
            "runnable_time_rate": self.runnable_time_rate,
            "qos_interactive": self.qos_interactive,
            "qos_interactive_rate": self.qos_interactive_rate,
            "gpu_time": self.gpu_time,
            "gpu_time_rate": self.gpu_time_rate,
            "zombie_children": self.zombie_children,
            "state": self.state,
            "priority": self.priority,
            "score": self.score,
            "band": self.band,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProcessScore":
        """Deserialize from dictionary."""
        return cls(
            pid=data["pid"],
            command=data["command"],
            captured_at=data["captured_at"],
            cpu_share=data["cpu_share"],
            gpu_share=data["gpu_share"],
            mem_share=data["mem_share"],
            disk_share=data["disk_share"],
            wakeups_share=data["wakeups_share"],
            disproportionality=data["disproportionality"],
            dominant_resource=data["dominant_resource"],
            cpu=data["cpu"],
            mem=data["mem"],
            mem_peak=data["mem_peak"],
            pageins=data["pageins"],
            pageins_rate=data["pageins_rate"],
            faults=data["faults"],
            faults_rate=data["faults_rate"],
            disk_io=data["disk_io"],
            disk_io_rate=data["disk_io_rate"],
            csw=data["csw"],
            csw_rate=data["csw_rate"],
            syscalls=data["syscalls"],
            syscalls_rate=data["syscalls_rate"],
            threads=data["threads"],
            mach_msgs=data["mach_msgs"],
            mach_msgs_rate=data["mach_msgs_rate"],
            instructions=data["instructions"],
            cycles=data["cycles"],
            ipc=data["ipc"],
            energy=data["energy"],
            energy_rate=data["energy_rate"],
            wakeups=data["wakeups"],
            wakeups_rate=data["wakeups_rate"],
            runnable_time=data["runnable_time"],
            runnable_time_rate=data["runnable_time_rate"],
            qos_interactive=data["qos_interactive"],
            qos_interactive_rate=data["qos_interactive_rate"],
            gpu_time=data["gpu_time"],
            gpu_time_rate=data["gpu_time_rate"],
            zombie_children=data["zombie_children"],
            state=data["state"],
            priority=data["priority"],
            score=data["score"],
            band=data["band"],
        )
```

**Step 4: Run test to verify it passes**

```bash
timeout 10s uv run pytest tests/test_collector.py::test_process_score_has_resource_shares tests/test_collector.py::test_process_score_no_category_scores -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/rogue_hunter/collector.py tests/test_collector.py
git commit -m "$(cat <<'EOF'
feat: Update ProcessScore to resource-based scoring fields

The scoring model changes from 4 categories (blocking/contention/pressure/
efficiency) to resource-based shares (cpu/gpu/memory/disk/wakeups).

Changes:
- Remove blocking_score, contention_score, pressure_score, efficiency_score
- Remove dominant_category, dominant_metrics
- Add cpu_share, gpu_share, mem_share, disk_share, wakeups_share
- Add disproportionality (highest share) and dominant_resource
- Update to_dict() and from_dict() for new fields
EOF
)"
```

---

## Task 4: Update Storage Schema to v18

**Context:** The ProcessScore changes require matching schema changes. This bumps the schema version, which triggers automatic DB recreation per project philosophy.

**Files:**
- Modify: `src/rogue_hunter/storage.py`
- Test: `tests/test_storage.py`

**Step 1: Write the failing test**

```python
# tests/test_storage.py

def test_schema_version_is_18():
    """Schema version is 18 for resource-based scoring."""
    from rogue_hunter.storage import SCHEMA_VERSION

    assert SCHEMA_VERSION == 18


def test_process_snapshots_has_resource_shares():
    """process_snapshots table has resource share columns."""
    from rogue_hunter.storage import SCHEMA

    # New columns should exist
    assert "cpu_share" in SCHEMA
    assert "gpu_share" in SCHEMA
    assert "mem_share" in SCHEMA
    assert "disk_share" in SCHEMA
    assert "wakeups_share" in SCHEMA
    assert "disproportionality" in SCHEMA
    assert "dominant_resource" in SCHEMA

    # Old columns should not exist
    assert "blocking_score" not in SCHEMA
    assert "contention_score" not in SCHEMA
    assert "pressure_score" not in SCHEMA
    assert "efficiency_score" not in SCHEMA
    assert "dominant_category" not in SCHEMA
    assert "dominant_metrics" not in SCHEMA
```

**Step 2: Run test to verify it fails**

```bash
timeout 10s uv run pytest tests/test_storage.py::test_schema_version_is_18 -v
```
Expected: FAIL with `AssertionError` (SCHEMA_VERSION is 17, not 18)

**Step 3: Write minimal implementation**

```python
# src/rogue_hunter/storage.py

# Update SCHEMA_VERSION:
SCHEMA_VERSION = 18

# Update SCHEMA - replace the process_snapshots table definition:
# Find the CREATE TABLE process_snapshots section and replace with:

CREATE TABLE process_snapshots (
    snapshot_id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES process_events(event_id) ON DELETE CASCADE,
    snapshot_type TEXT NOT NULL,  -- 'entry', 'exit', 'checkpoint'
    captured_at REAL NOT NULL,

    -- Resource shares (multiples of fair share)
    cpu_share REAL NOT NULL,
    gpu_share REAL NOT NULL,
    mem_share REAL NOT NULL,
    disk_share REAL NOT NULL,
    wakeups_share REAL NOT NULL,
    disproportionality REAL NOT NULL,
    dominant_resource TEXT NOT NULL,

    -- Raw metrics
    cpu REAL NOT NULL,
    mem INTEGER NOT NULL,
    mem_peak INTEGER NOT NULL,
    pageins INTEGER NOT NULL,
    pageins_rate REAL NOT NULL,
    faults INTEGER NOT NULL,
    faults_rate REAL NOT NULL,
    disk_io INTEGER NOT NULL,
    disk_io_rate REAL NOT NULL,
    csw INTEGER NOT NULL,
    csw_rate REAL NOT NULL,
    syscalls INTEGER NOT NULL,
    syscalls_rate REAL NOT NULL,
    threads INTEGER NOT NULL,
    mach_msgs INTEGER NOT NULL,
    mach_msgs_rate REAL NOT NULL,
    instructions INTEGER NOT NULL,
    cycles INTEGER NOT NULL,
    ipc REAL NOT NULL,
    energy INTEGER NOT NULL,
    energy_rate REAL NOT NULL,
    wakeups INTEGER NOT NULL,
    wakeups_rate REAL NOT NULL,
    runnable_time INTEGER NOT NULL,
    runnable_time_rate REAL NOT NULL,
    qos_interactive INTEGER NOT NULL,
    qos_interactive_rate REAL NOT NULL,
    gpu_time INTEGER NOT NULL,
    gpu_time_rate REAL NOT NULL,
    zombie_children INTEGER NOT NULL,
    state TEXT NOT NULL,
    priority INTEGER NOT NULL,

    -- Final score
    score INTEGER NOT NULL,
    band TEXT NOT NULL
);

# Update insert_process_snapshot() to use new fields:

def insert_process_snapshot(
    conn: sqlite3.Connection,
    event_id: int,
    snapshot_type: str,
    score: "ProcessScore",
) -> int:
    """Insert a process snapshot and return snapshot_id."""
    cursor = conn.execute(
        """
        INSERT INTO process_snapshots (
            event_id, snapshot_type, captured_at,
            cpu_share, gpu_share, mem_share, disk_share, wakeups_share,
            disproportionality, dominant_resource,
            cpu, mem, mem_peak,
            pageins, pageins_rate, faults, faults_rate,
            disk_io, disk_io_rate, csw, csw_rate,
            syscalls, syscalls_rate, threads,
            mach_msgs, mach_msgs_rate,
            instructions, cycles, ipc,
            energy, energy_rate, wakeups, wakeups_rate,
            runnable_time, runnable_time_rate,
            qos_interactive, qos_interactive_rate,
            gpu_time, gpu_time_rate,
            zombie_children, state, priority, score, band
        ) VALUES (
            ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?, ?, ?, ?
        )
        """,
        (
            event_id, snapshot_type, score.captured_at,
            score.cpu_share, score.gpu_share, score.mem_share, score.disk_share, score.wakeups_share,
            score.disproportionality, score.dominant_resource,
            score.cpu, score.mem, score.mem_peak,
            score.pageins, score.pageins_rate, score.faults, score.faults_rate,
            score.disk_io, score.disk_io_rate, score.csw, score.csw_rate,
            score.syscalls, score.syscalls_rate, score.threads,
            score.mach_msgs, score.mach_msgs_rate,
            score.instructions, score.cycles, score.ipc,
            score.energy, score.energy_rate, score.wakeups, score.wakeups_rate,
            score.runnable_time, score.runnable_time_rate,
            score.qos_interactive, score.qos_interactive_rate,
            score.gpu_time, score.gpu_time_rate,
            score.zombie_children, score.state, score.priority, score.score, score.band,
        ),
    )
    conn.commit()
    return cursor.lastrowid


# Update get_snapshot() and get_process_snapshots() to return new fields
# (replace dominant_category/dominant_metrics with dominant_resource, etc.)
```

**Step 4: Run test to verify it passes**

```bash
timeout 10s uv run pytest tests/test_storage.py::test_schema_version_is_18 tests/test_storage.py::test_process_snapshots_has_resource_shares -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/rogue_hunter/storage.py tests/test_storage.py
git commit -m "$(cat <<'EOF'
feat: Update storage schema to v18 for resource-based scoring

Schema changes to match new ProcessScore fields. Old databases will be
automatically deleted and recreated (per project philosophy: no migrations).

Changes:
- Bump SCHEMA_VERSION to 18
- Replace category score columns with resource share columns
- Update insert_process_snapshot() for new fields
- Update get_snapshot() and get_process_snapshots() for new fields
EOF
)"
```

---

## Task 5: Implement Active Process Counting

**Context:** Fair share calculation requires knowing how many processes are "active." A process is active if it's in a non-idle state AND using measurable resources.

**Files:**
- Modify: `src/rogue_hunter/collector.py`
- Test: `tests/test_collector.py`

**Step 1: Write the failing test**

```python
# tests/test_collector.py

def test_count_active_processes_excludes_idle():
    """Idle processes are not counted as active."""
    from rogue_hunter.collector import count_active_processes
    from rogue_hunter.config import ScoringConfig

    processes = [
        {"state": "running", "cpu": 5.0, "mem": 100_000_000, "disk_io_rate": 0},
        {"state": "idle", "cpu": 1.0, "mem": 50_000_000, "disk_io_rate": 0},  # idle = excluded
        {"state": "sleeping", "cpu": 0.5, "mem": 200_000_000, "disk_io_rate": 100},
    ]
    config = ScoringConfig()

    count = count_active_processes(processes, config)

    assert count == 2  # idle process excluded


def test_count_active_processes_excludes_no_resources():
    """Processes using no resources are not counted as active."""
    from rogue_hunter.collector import count_active_processes
    from rogue_hunter.config import ScoringConfig

    processes = [
        {"state": "running", "cpu": 5.0, "mem": 100_000_000, "disk_io_rate": 0},
        {"state": "sleeping", "cpu": 0.0, "mem": 0, "disk_io_rate": 0},  # no resources = excluded
        {"state": "running", "cpu": 0.0, "mem": 0, "disk_io_rate": 1000},  # has disk I/O = included
    ]
    config = ScoringConfig()

    count = count_active_processes(processes, config)

    assert count == 2  # zero-resource process excluded


def test_count_active_processes_respects_thresholds():
    """Active thresholds from config are respected."""
    from rogue_hunter.collector import count_active_processes
    from rogue_hunter.config import ScoringConfig

    processes = [
        {"state": "running", "cpu": 0.05, "mem": 5_000_000, "disk_io_rate": 0},  # below thresholds
        {"state": "running", "cpu": 0.2, "mem": 5_000_000, "disk_io_rate": 0},  # cpu above threshold
    ]
    config = ScoringConfig(active_min_cpu=0.1, active_min_memory_mb=10.0, active_min_disk_io=0)

    count = count_active_processes(processes, config)

    assert count == 1  # only process with cpu > 0.1 counts


def test_count_active_processes_minimum_one():
    """Active process count is at least 1 to avoid division by zero."""
    from rogue_hunter.collector import count_active_processes
    from rogue_hunter.config import ScoringConfig

    processes = []  # No processes
    config = ScoringConfig()

    count = count_active_processes(processes, config)

    assert count == 1  # Minimum of 1
```

**Step 2: Run test to verify it fails**

```bash
timeout 10s uv run pytest tests/test_collector.py::test_count_active_processes_excludes_idle -v
```
Expected: FAIL with `ImportError` (count_active_processes doesn't exist)

**Step 3: Write minimal implementation**

```python
# src/rogue_hunter/collector.py

def count_active_processes(processes: list[dict], config: "ScoringConfig") -> int:
    """Count processes that are considered 'active' for fair share calculation.

    A process is active if:
    1. State is NOT idle (running, sleeping, stopped, zombie, stuck all count)
    2. AND using measurable resources (CPU > threshold OR memory > threshold OR disk I/O > 0)

    Returns at least 1 to avoid division by zero in fair share calculation.
    """
    count = 0
    mem_threshold_bytes = config.active_min_memory_mb * 1_000_000

    for proc in processes:
        # Must be non-idle
        if proc.get("state") == "idle":
            continue

        # Must be using some resources
        cpu = proc.get("cpu", 0)
        mem = proc.get("mem", 0)
        disk_io_rate = proc.get("disk_io_rate", 0)

        uses_cpu = cpu >= config.active_min_cpu
        uses_memory = mem >= mem_threshold_bytes
        uses_disk = disk_io_rate > config.active_min_disk_io

        if uses_cpu or uses_memory or uses_disk:
            count += 1

    return max(1, count)  # Minimum 1 to avoid division by zero
```

**Step 4: Run test to verify it passes**

```bash
timeout 10s uv run pytest tests/test_collector.py::test_count_active_processes_excludes_idle tests/test_collector.py::test_count_active_processes_excludes_no_resources tests/test_collector.py::test_count_active_processes_respects_thresholds tests/test_collector.py::test_count_active_processes_minimum_one -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/rogue_hunter/collector.py tests/test_collector.py
git commit -m "$(cat <<'EOF'
feat: Add active process counting for fair share calculation

Fair share = 1 / active_process_count. A process is "active" if it's
in a non-idle state AND using measurable resources (CPU, memory, or disk).

Changes:
- Add count_active_processes() function
- Respects active_min_cpu, active_min_memory_mb, active_min_disk_io from config
- Returns minimum of 1 to prevent division by zero
EOF
)"
```

---

## Task 6: Implement Fair Share Calculation

**Context:** For each resource type, calculate total system usage and each process's share compared to "fair share" (1 ÷ active count).

**Files:**
- Modify: `src/rogue_hunter/collector.py`
- Test: `tests/test_collector.py`

**Step 1: Write the failing test**

```python
# tests/test_collector.py

def test_calculate_resource_shares_basic():
    """Resource shares are calculated as multiples of fair share."""
    from rogue_hunter.collector import calculate_resource_shares

    processes = [
        {"pid": 1, "cpu": 50.0, "gpu_time_rate": 0, "mem": 1_000_000_000, "disk_io_rate": 1000, "wakeups_rate": 10},
        {"pid": 2, "cpu": 50.0, "gpu_time_rate": 0, "mem": 1_000_000_000, "disk_io_rate": 1000, "wakeups_rate": 10},
    ]
    active_count = 2

    shares = calculate_resource_shares(processes, active_count)

    # Each process uses 50% of total CPU (50 / 100 total)
    # Fair share = 1/2 = 50%
    # Share ratio = 50% / 50% = 1.0 (exactly fair)
    assert shares[1]["cpu_share"] == 1.0
    assert shares[2]["cpu_share"] == 1.0


def test_calculate_resource_shares_disproportionate():
    """Process using more than fair share has share > 1."""
    from rogue_hunter.collector import calculate_resource_shares

    processes = [
        {"pid": 1, "cpu": 90.0, "gpu_time_rate": 0, "mem": 500_000_000, "disk_io_rate": 0, "wakeups_rate": 0},
        {"pid": 2, "cpu": 10.0, "gpu_time_rate": 0, "mem": 500_000_000, "disk_io_rate": 0, "wakeups_rate": 0},
    ]
    active_count = 2

    shares = calculate_resource_shares(processes, active_count)

    # Process 1: 90% of 100% total = 90% usage
    # Fair share = 50%
    # Share ratio = 90% / 50% = 1.8
    assert shares[1]["cpu_share"] == 1.8
    # Process 2: 10% / 50% = 0.2
    assert shares[2]["cpu_share"] == 0.2


def test_calculate_resource_shares_zero_total():
    """When total resource is zero, all shares are zero."""
    from rogue_hunter.collector import calculate_resource_shares

    processes = [
        {"pid": 1, "cpu": 0, "gpu_time_rate": 0, "mem": 1_000_000, "disk_io_rate": 0, "wakeups_rate": 0},
        {"pid": 2, "cpu": 0, "gpu_time_rate": 0, "mem": 1_000_000, "disk_io_rate": 0, "wakeups_rate": 0},
    ]
    active_count = 2

    shares = calculate_resource_shares(processes, active_count)

    # No CPU usage, so CPU share is 0
    assert shares[1]["cpu_share"] == 0.0
    assert shares[2]["cpu_share"] == 0.0


def test_calculate_resource_shares_all_resources():
    """Shares calculated for all resource types."""
    from rogue_hunter.collector import calculate_resource_shares

    processes = [
        {"pid": 1, "cpu": 100, "gpu_time_rate": 50, "mem": 2_000_000_000, "disk_io_rate": 5000, "wakeups_rate": 100},
    ]
    active_count = 1

    shares = calculate_resource_shares(processes, active_count)

    # Single process = uses 100% of all resources = 1.0 share (exactly fair when alone)
    assert shares[1]["cpu_share"] == 1.0
    assert shares[1]["gpu_share"] == 1.0
    assert shares[1]["mem_share"] == 1.0
    assert shares[1]["disk_share"] == 1.0
    assert shares[1]["wakeups_share"] == 1.0
```

**Step 2: Run test to verify it fails**

```bash
timeout 10s uv run pytest tests/test_collector.py::test_calculate_resource_shares_basic -v
```
Expected: FAIL with `ImportError` (calculate_resource_shares doesn't exist)

**Step 3: Write minimal implementation**

```python
# src/rogue_hunter/collector.py

def calculate_resource_shares(
    processes: list[dict],
    active_count: int,
) -> dict[int, dict[str, float]]:
    """Calculate resource shares for each process.

    For each resource type, calculates:
    1. Total system usage across all processes
    2. Fair share = 1 / active_count (as a fraction of total)
    3. Each process's share ratio = (process usage / total) / fair_share

    A share of 1.0 means the process uses exactly its fair share.
    A share of 10.0 means the process uses 10x its fair share.

    Returns dict mapping PID to dict of resource shares.
    """
    fair_share = 1.0 / active_count

    # Calculate totals
    total_cpu = sum(p.get("cpu", 0) for p in processes)
    total_gpu = sum(p.get("gpu_time_rate", 0) for p in processes)
    total_mem = sum(p.get("mem", 0) for p in processes)
    total_disk = sum(p.get("disk_io_rate", 0) for p in processes)
    total_wakeups = sum(p.get("wakeups_rate", 0) for p in processes)

    result = {}
    for proc in processes:
        pid = proc["pid"]

        # Calculate usage fraction for each resource (0.0 to 1.0)
        cpu_fraction = proc.get("cpu", 0) / total_cpu if total_cpu > 0 else 0
        gpu_fraction = proc.get("gpu_time_rate", 0) / total_gpu if total_gpu > 0 else 0
        mem_fraction = proc.get("mem", 0) / total_mem if total_mem > 0 else 0
        disk_fraction = proc.get("disk_io_rate", 0) / total_disk if total_disk > 0 else 0
        wakeups_fraction = proc.get("wakeups_rate", 0) / total_wakeups if total_wakeups > 0 else 0

        # Calculate share ratio (multiples of fair share)
        result[pid] = {
            "cpu_share": cpu_fraction / fair_share if fair_share > 0 else 0,
            "gpu_share": gpu_fraction / fair_share if fair_share > 0 else 0,
            "mem_share": mem_fraction / fair_share if fair_share > 0 else 0,
            "disk_share": disk_fraction / fair_share if fair_share > 0 else 0,
            "wakeups_share": wakeups_fraction / fair_share if fair_share > 0 else 0,
        }

    return result
```

**Step 4: Run test to verify it passes**

```bash
timeout 10s uv run pytest tests/test_collector.py::test_calculate_resource_shares_basic tests/test_collector.py::test_calculate_resource_shares_disproportionate tests/test_collector.py::test_calculate_resource_shares_zero_total tests/test_collector.py::test_calculate_resource_shares_all_resources -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/rogue_hunter/collector.py tests/test_collector.py
git commit -m "$(cat <<'EOF'
feat: Add fair share calculation for resource scoring

Calculates how many multiples of "fair share" each process consumes
for each resource type. Fair share = 1 / active_process_count.

Changes:
- Add calculate_resource_shares() function
- Returns shares for cpu, gpu, memory, disk, wakeups
- Handles zero totals gracefully (returns 0 share)
EOF
)"
```

---

## Task 7: Implement Disproportionate-Share Scoring

**Context:** Replace `_score_process()` with the new scoring model. Apply resource weights, use logarithmic curve, determine dominant resource.

**Files:**
- Modify: `src/rogue_hunter/collector.py`
- Test: `tests/test_collector.py`

**Step 1: Write the failing test**

```python
# tests/test_collector.py

def test_score_from_shares_applies_weights():
    """Score calculation applies resource weights from config."""
    from rogue_hunter.collector import score_from_shares
    from rogue_hunter.config import ResourceWeights

    shares = {
        "cpu_share": 10.0,  # 10x fair share
        "gpu_share": 0.0,
        "mem_share": 0.0,
        "disk_share": 0.0,
        "wakeups_share": 0.0,
    }
    weights = ResourceWeights(cpu=1.0, gpu=3.0, memory=1.0, disk_io=1.0, wakeups=2.0)

    score, dominant, disproportionality = score_from_shares(shares, weights)

    assert dominant == "cpu"
    assert disproportionality == 10.0
    assert score > 0


def test_score_from_shares_gpu_weighted_higher():
    """GPU share contributes more to score than equal CPU share."""
    from rogue_hunter.collector import score_from_shares
    from rogue_hunter.config import ResourceWeights

    weights = ResourceWeights(cpu=1.0, gpu=3.0, memory=1.0, disk_io=1.0, wakeups=2.0)

    cpu_shares = {"cpu_share": 10.0, "gpu_share": 0.0, "mem_share": 0.0, "disk_share": 0.0, "wakeups_share": 0.0}
    gpu_shares = {"cpu_share": 0.0, "gpu_share": 10.0, "mem_share": 0.0, "disk_share": 0.0, "wakeups_share": 0.0}

    cpu_score, _, _ = score_from_shares(cpu_shares, weights)
    gpu_score, _, _ = score_from_shares(gpu_shares, weights)

    assert gpu_score > cpu_score  # GPU weighted 3x, so higher score


def test_score_from_shares_logarithmic_curve():
    """Score uses logarithmic curve - diminishing returns at extremes."""
    from rogue_hunter.collector import score_from_shares
    from rogue_hunter.config import ResourceWeights

    weights = ResourceWeights()

    # 10x fair share
    shares_10x = {"cpu_share": 10.0, "gpu_share": 0.0, "mem_share": 0.0, "disk_share": 0.0, "wakeups_share": 0.0}
    # 100x fair share
    shares_100x = {"cpu_share": 100.0, "gpu_share": 0.0, "mem_share": 0.0, "disk_share": 0.0, "wakeups_share": 0.0}
    # 1000x fair share
    shares_1000x = {"cpu_share": 1000.0, "gpu_share": 0.0, "mem_share": 0.0, "disk_share": 0.0, "wakeups_share": 0.0}

    score_10x, _, _ = score_from_shares(shares_10x, weights)
    score_100x, _, _ = score_from_shares(shares_100x, weights)
    score_1000x, _, _ = score_from_shares(shares_1000x, weights)

    # Logarithmic: 10x jump from 10 to 100 should give similar increase as 100 to 1000
    increase_10_to_100 = score_100x - score_10x
    increase_100_to_1000 = score_1000x - score_100x

    # Not exactly equal due to curve shape, but should be in same ballpark
    assert increase_100_to_1000 < increase_10_to_100 * 2  # Diminishing returns


def test_score_from_shares_critical_reachable():
    """Critical band (70+) is reachable with extreme disproportionality."""
    from rogue_hunter.collector import score_from_shares
    from rogue_hunter.config import ResourceWeights

    weights = ResourceWeights()

    # ~200x fair share should reach critical
    shares = {"cpu_share": 200.0, "gpu_share": 0.0, "mem_share": 0.0, "disk_share": 0.0, "wakeups_share": 0.0}

    score, _, _ = score_from_shares(shares, weights)

    assert score >= 70  # Critical band


def test_score_from_shares_high_reachable_under_load():
    """High band (50-69) is reachable with moderate disproportionality."""
    from rogue_hunter.collector import score_from_shares
    from rogue_hunter.config import ResourceWeights

    weights = ResourceWeights()

    # ~50-100x fair share should reach high
    shares = {"cpu_share": 75.0, "gpu_share": 0.0, "mem_share": 0.0, "disk_share": 0.0, "wakeups_share": 0.0}

    score, _, _ = score_from_shares(shares, weights)

    assert 50 <= score < 70  # High band


def test_score_from_shares_dominant_resource():
    """Dominant resource is the one with highest weighted share."""
    from rogue_hunter.collector import score_from_shares
    from rogue_hunter.config import ResourceWeights

    weights = ResourceWeights(cpu=1.0, gpu=3.0, memory=1.0, disk_io=1.0, wakeups=2.0)

    # GPU has lower raw share but higher weight
    shares = {"cpu_share": 10.0, "gpu_share": 5.0, "mem_share": 2.0, "disk_share": 1.0, "wakeups_share": 1.0}

    _, dominant, disproportionality = score_from_shares(shares, weights)

    # GPU: 5.0 * 3.0 = 15.0 weighted
    # CPU: 10.0 * 1.0 = 10.0 weighted
    assert dominant == "gpu"
    assert disproportionality == 5.0  # Raw share of dominant resource


def test_score_from_shares_clamped_to_100():
    """Score is clamped to maximum of 100."""
    from rogue_hunter.collector import score_from_shares
    from rogue_hunter.config import ResourceWeights

    weights = ResourceWeights()

    # Extreme disproportionality
    shares = {"cpu_share": 10000.0, "gpu_share": 10000.0, "mem_share": 10000.0, "disk_share": 10000.0, "wakeups_share": 10000.0}

    score, _, _ = score_from_shares(shares, weights)

    assert score == 100
```

**Step 2: Run test to verify it fails**

```bash
timeout 10s uv run pytest tests/test_collector.py::test_score_from_shares_applies_weights -v
```
Expected: FAIL with `ImportError` (score_from_shares doesn't exist)

**Step 3: Write minimal implementation**

```python
# src/rogue_hunter/collector.py

import math

def score_from_shares(
    shares: dict[str, float],
    weights: "ResourceWeights",
) -> tuple[int, str, float]:
    """Calculate score from resource shares using Apple-style weighting.

    Uses logarithmic curve to map disproportionality to 0-100 score:
    - High band (50-69) reachable at ~50-100x fair share
    - Critical band (70+) reachable at ~200x fair share

    Args:
        shares: Dict with cpu_share, gpu_share, mem_share, disk_share, wakeups_share
        weights: ResourceWeights with weight multipliers for each resource

    Returns:
        Tuple of (score 0-100, dominant_resource, disproportionality)
    """
    # Calculate weighted contributions
    weighted = {
        "cpu": shares["cpu_share"] * weights.cpu,
        "gpu": shares["gpu_share"] * weights.gpu,
        "memory": shares["mem_share"] * weights.memory,
        "disk": shares["disk_share"] * weights.disk_io,
        "wakeups": shares["wakeups_share"] * weights.wakeups,
    }

    # Find dominant resource (highest weighted contribution)
    dominant = max(weighted, key=weighted.get)

    # Map resource name to share key for disproportionality
    share_key_map = {
        "cpu": "cpu_share",
        "gpu": "gpu_share",
        "memory": "mem_share",
        "disk": "disk_share",
        "wakeups": "wakeups_share",
    }
    disproportionality = shares[share_key_map[dominant]]

    # Sum weighted contributions
    total_weighted = sum(weighted.values())

    # Apply logarithmic curve
    # log2(1) = 0, log2(2) = 1, log2(50) ≈ 5.6, log2(100) ≈ 6.6, log2(200) ≈ 7.6
    # Scale: multiply by ~10 to get score range
    # Target: 50x -> ~56, 100x -> ~66 (high band), 200x -> ~76 (critical)
    if total_weighted <= 1.0:
        # At or below fair share = score 0
        raw_score = 0.0
    else:
        # Logarithmic scaling
        # log2(total_weighted) gives us the "order of magnitude" above fair share
        # Multiply by scaling factor to spread across 0-100 range
        raw_score = math.log2(total_weighted) * 10.0

    # Clamp to 0-100
    score = max(0, min(100, int(raw_score)))

    return score, dominant, disproportionality
```

**Step 4: Run test to verify it passes**

```bash
timeout 10s uv run pytest tests/test_collector.py::test_score_from_shares_applies_weights tests/test_collector.py::test_score_from_shares_gpu_weighted_higher tests/test_collector.py::test_score_from_shares_logarithmic_curve tests/test_collector.py::test_score_from_shares_critical_reachable tests/test_collector.py::test_score_from_shares_high_reachable_under_load tests/test_collector.py::test_score_from_shares_dominant_resource tests/test_collector.py::test_score_from_shares_clamped_to_100 -v
```
Expected: PASS (may need to tune the scaling factor to hit exact band targets)

**Step 5: Commit**

```bash
git add src/rogue_hunter/collector.py tests/test_collector.py
git commit -m "$(cat <<'EOF'
feat: Add disproportionate-share scoring with logarithmic curve

Replaces the old 4-category scoring with resource-based scoring.
Uses logarithmic curve so high band is reachable under load (~50-100x
fair share) while critical remains rare (~200x+).

Changes:
- Add score_from_shares() function
- Apply resource weights from config
- Use log2 curve for diminishing returns at extremes
- Return dominant resource and disproportionality
EOF
)"
```

---

## Task 8: Integrate New Scoring into Collector

**Context:** Wire up the new scoring functions in `LibprocCollector._collect_sync()`. The flow becomes: collect metrics → count active → calculate shares → score → build ProcessScore.

**Files:**
- Modify: `src/rogue_hunter/collector.py`
- Test: `tests/test_collector.py`

**Step 1: Write the failing test**

```python
# tests/test_collector.py

import pytest
from unittest.mock import patch, MagicMock

def test_collector_uses_new_scoring():
    """LibprocCollector uses resource-based scoring."""
    from rogue_hunter.collector import LibprocCollector, ProcessScore
    from rogue_hunter.config import Config

    config = Config()
    collector = LibprocCollector(config)

    # Mock libproc to return controlled data
    mock_pids = [100, 200]
    mock_rusage = {
        "ri_phys_footprint": 100_000_000,
        "ri_lifetime_max_phys_footprint": 200_000_000,
        "ri_pageins": 10,
        "ri_diskio_bytesread": 1000,
        "ri_diskio_byteswritten": 500,
        "ri_pkg_idle_wkups": 5,
        "ri_interrupt_wkups": 2,
        "ri_user_time": 1_000_000_000,  # 1 sec
        "ri_system_time": 500_000_000,   # 0.5 sec
        "ri_runnable_time": 100_000_000,
        "ri_cpu_time_qos_user_interactive": 50_000_000,
        "ri_billed_energy": 1000,
        "ri_instructions": 1_000_000,
        "ri_cycles": 2_000_000,
    }
    mock_task_info = {
        "pti_faults": 100,
        "pti_csw": 50,
        "pti_syscalls_mach": 20,
        "pti_syscalls_unix": 30,
        "pti_threadnum": 4,
        "pti_messages_sent": 10,
        "pti_messages_received": 5,
        "pti_priority": 31,
    }
    mock_bsd_info = {"pbi_status": 2}  # Running

    with patch("rogue_hunter.collector.list_all_pids", return_value=mock_pids), \
         patch("rogue_hunter.collector.get_rusage", return_value=mock_rusage), \
         patch("rogue_hunter.collector.get_task_info", return_value=mock_task_info), \
         patch("rogue_hunter.collector.get_bsd_info", return_value=mock_bsd_info), \
         patch("rogue_hunter.collector.get_process_name", return_value="test_process"), \
         patch("rogue_hunter.collector.get_state_name", return_value="running"), \
         patch("rogue_hunter.collector.get_gpu_usage", return_value={}):

        # First collect to establish baseline
        collector._collect_sync()
        # Second collect to get rates
        samples = collector._collect_sync()

    assert len(samples.rogues) > 0
    score = samples.rogues[0]

    # Verify new fields exist
    assert hasattr(score, 'cpu_share')
    assert hasattr(score, 'dominant_resource')
    assert hasattr(score, 'disproportionality')

    # Verify old fields don't exist
    assert not hasattr(score, 'blocking_score')
    assert not hasattr(score, 'dominant_category')


def test_collector_calculates_active_count():
    """Collector calculates active process count for fair share."""
    from rogue_hunter.collector import LibprocCollector
    from rogue_hunter.config import Config

    config = Config()
    collector = LibprocCollector(config)

    # Create test data with mix of active/inactive
    processes = [
        {"pid": 1, "state": "running", "cpu": 50.0, "mem": 100_000_000, "disk_io_rate": 0},
        {"pid": 2, "state": "idle", "cpu": 0.0, "mem": 1_000_000, "disk_io_rate": 0},
        {"pid": 3, "state": "sleeping", "cpu": 0.0, "mem": 0, "disk_io_rate": 0},
    ]

    from rogue_hunter.collector import count_active_processes
    active = count_active_processes(processes, config.scoring)

    # Only pid 1 is active (non-idle and using resources)
    assert active == 1
```

**Step 2: Run test to verify it fails**

```bash
timeout 10s uv run pytest tests/test_collector.py::test_collector_uses_new_scoring -v
```
Expected: FAIL (collector still uses old scoring, ProcessScore has old fields)

**Step 3: Write minimal implementation**

```python
# src/rogue_hunter/collector.py

# In LibprocCollector._collect_sync(), replace the scoring section with:

def _collect_sync(self) -> ProcessSamples:
    """Synchronous collection - runs in executor."""
    start = time.monotonic()
    now = time.time()

    # ... existing code to collect PIDs and raw metrics ...
    # (keep all the libproc/IOKit data collection)

    # After collecting all process data into `all_procs` list:

    # Count active processes for fair share calculation
    active_count = count_active_processes(all_procs, self._config.scoring)

    # Calculate resource shares for all processes
    shares_by_pid = calculate_resource_shares(all_procs, active_count)

    # Score all processes
    scored: list[ProcessScore] = []
    for proc in all_procs:
        pid = proc["pid"]
        shares = shares_by_pid.get(pid, {
            "cpu_share": 0, "gpu_share": 0, "mem_share": 0,
            "disk_share": 0, "wakeups_share": 0
        })

        # Calculate score from shares
        score, dominant_resource, disproportionality = score_from_shares(
            shares,
            self._config.scoring.resource_weights
        )

        # Apply state multiplier
        state = proc.get("state", "running")
        state_mult = self._config.scoring.state_multipliers.get(state)
        final_score = max(0, min(100, int(score * state_mult)))

        # Determine band
        band = self._config.bands.get_band(final_score)

        # Build ProcessScore
        process_score = ProcessScore(
            pid=pid,
            command=proc["command"],
            captured_at=now,
            # Resource shares
            cpu_share=shares["cpu_share"],
            gpu_share=shares["gpu_share"],
            mem_share=shares["mem_share"],
            disk_share=shares["disk_share"],
            wakeups_share=shares["wakeups_share"],
            disproportionality=disproportionality,
            dominant_resource=dominant_resource,
            # Raw metrics (from proc dict)
            cpu=proc.get("cpu", 0),
            mem=proc.get("mem", 0),
            mem_peak=proc.get("mem_peak", 0),
            pageins=proc.get("pageins", 0),
            pageins_rate=proc.get("pageins_rate", 0),
            faults=proc.get("faults", 0),
            faults_rate=proc.get("faults_rate", 0),
            disk_io=proc.get("disk_io", 0),
            disk_io_rate=proc.get("disk_io_rate", 0),
            csw=proc.get("csw", 0),
            csw_rate=proc.get("csw_rate", 0),
            syscalls=proc.get("syscalls", 0),
            syscalls_rate=proc.get("syscalls_rate", 0),
            threads=proc.get("threads", 0),
            mach_msgs=proc.get("mach_msgs", 0),
            mach_msgs_rate=proc.get("mach_msgs_rate", 0),
            instructions=proc.get("instructions", 0),
            cycles=proc.get("cycles", 0),
            ipc=proc.get("ipc", 0),
            energy=proc.get("energy", 0),
            energy_rate=proc.get("energy_rate", 0),
            wakeups=proc.get("wakeups", 0),
            wakeups_rate=proc.get("wakeups_rate", 0),
            runnable_time=proc.get("runnable_time", 0),
            runnable_time_rate=proc.get("runnable_time_rate", 0),
            qos_interactive=proc.get("qos_interactive", 0),
            qos_interactive_rate=proc.get("qos_interactive_rate", 0),
            gpu_time=proc.get("gpu_time", 0),
            gpu_time_rate=proc.get("gpu_time_rate", 0),
            zombie_children=proc.get("zombie_children", 0),
            state=state,
            priority=proc.get("priority", 0),
            score=final_score,
            band=band,
        )
        scored.append(process_score)

    # Select rogues (top N by score)
    rogues = self._select_rogues(scored)

    # ... rest of existing code to build ProcessSamples ...
```

**Step 4: Run test to verify it passes**

```bash
timeout 10s uv run pytest tests/test_collector.py::test_collector_uses_new_scoring tests/test_collector.py::test_collector_calculates_active_count -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/rogue_hunter/collector.py tests/test_collector.py
git commit -m "$(cat <<'EOF'
feat: Integrate new scoring into LibprocCollector

Wire up the disproportionate-share scoring in the collection flow:
collect metrics -> count active -> calculate shares -> score -> ProcessScore.

Changes:
- Update _collect_sync() to use count_active_processes()
- Use calculate_resource_shares() for fair share calculation
- Use score_from_shares() for final scoring
- Build ProcessScore with new resource share fields
EOF
)"
```

---

## Task 9: Remove Old Scoring Code

**Context:** Clean up the old scoring implementation now that new scoring is in place.

**Files:**
- Modify: `src/rogue_hunter/collector.py`
- Test: `tests/test_collector.py`

**Step 1: Write the failing test**

```python
# tests/test_collector.py

def test_old_scoring_methods_removed():
    """Old scoring methods no longer exist."""
    from rogue_hunter.collector import LibprocCollector

    # These methods should not exist
    assert not hasattr(LibprocCollector, '_score_process')
    assert not hasattr(LibprocCollector, '_get_dominant_metrics')


def test_get_core_count_removed():
    """Unused get_core_count function is removed."""
    from rogue_hunter import collector

    assert not hasattr(collector, 'get_core_count')
```

**Step 2: Run test to verify it fails**

```bash
timeout 10s uv run pytest tests/test_collector.py::test_old_scoring_methods_removed -v
```
Expected: FAIL with `AssertionError` (_score_process still exists)

**Step 3: Write minimal implementation**

```python
# src/rogue_hunter/collector.py

# DELETE the following methods and functions:

# 1. Delete get_core_count() function (around line 50)
# def get_core_count() -> int:
#     ...

# 2. Delete LibprocCollector._score_process() method
# def _score_process(self, proc: dict) -> ProcessScore:
#     ...

# 3. Delete LibprocCollector._get_dominant_metrics() method
# def _get_dominant_metrics(self, proc: dict, category: str) -> list[str]:
#     ...

# Note: Keep _select_rogues() and _get_band() as they're still used
```

**Step 4: Run test to verify it passes**

```bash
timeout 10s uv run pytest tests/test_collector.py::test_old_scoring_methods_removed tests/test_collector.py::test_get_core_count_removed -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/rogue_hunter/collector.py tests/test_collector.py
git commit -m "$(cat <<'EOF'
refactor: Remove old category-based scoring code

The new resource-based scoring replaces the old 4-category system.
Clean up dead code.

Changes:
- Remove _score_process() method
- Remove _get_dominant_metrics() method
- Remove unused get_core_count() function
EOF
)"
```

---

## Task 10: Implement Graduated Capture Frequency

**Context:** Different bands need different checkpoint frequencies. Low = none, Medium = every N samples, Elevated = every M samples, High/Critical = every sample.

**Files:**
- Modify: `src/rogue_hunter/tracker.py`
- Test: `tests/test_tracker.py`

**Step 1: Write the failing test**

```python
# tests/test_tracker.py

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

def test_low_band_not_tracked():
    """Processes in low band are not tracked."""
    from rogue_hunter.tracker import ProcessTracker
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.collector import ProcessScore

    conn = MagicMock()
    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1000)

    # Process in low band (score 15)
    score = _make_process_score(pid=100, score=15, band="low")

    tracker.update([score])

    # Should not create event
    assert 100 not in tracker.tracked
    conn.execute.assert_not_called()


def test_medium_band_checkpoints_every_n_samples():
    """Medium band checkpoints every N samples."""
    from rogue_hunter.tracker import ProcessTracker
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.collector import ProcessScore

    conn = MagicMock()
    conn.execute.return_value.lastrowid = 1
    bands = BandsConfig(medium_checkpoint_samples=3)  # Checkpoint every 3 samples
    tracker = ProcessTracker(conn, bands, boot_time=1000)

    score = _make_process_score(pid=100, score=25, band="medium")

    # Sample 1: Creates event + entry snapshot
    tracker.update([score])
    initial_calls = conn.execute.call_count

    # Samples 2-3: No checkpoint yet
    tracker.update([score])
    tracker.update([score])
    assert conn.execute.call_count == initial_calls  # No new inserts

    # Sample 4: Checkpoint (3 samples since last)
    tracker.update([score])
    assert conn.execute.call_count > initial_calls  # Checkpoint inserted


def test_elevated_band_checkpoints_more_frequently():
    """Elevated band checkpoints more frequently than medium."""
    from rogue_hunter.tracker import ProcessTracker
    from rogue_hunter.config import BandsConfig

    conn = MagicMock()
    conn.execute.return_value.lastrowid = 1
    bands = BandsConfig(medium_checkpoint_samples=20, elevated_checkpoint_samples=10)
    tracker = ProcessTracker(conn, bands, boot_time=1000)

    medium_score = _make_process_score(pid=100, score=25, band="medium")
    elevated_score = _make_process_score(pid=200, score=45, band="elevated")

    # Start tracking both
    tracker.update([medium_score, elevated_score])

    # After 10 samples, elevated should checkpoint but not medium
    for _ in range(10):
        tracker.update([medium_score, elevated_score])

    # Elevated (pid 200) should have more checkpoints than medium (pid 100)
    # Check via sample_count on tracked processes
    assert tracker.tracked[200].samples_since_checkpoint == 0  # Just checkpointed
    assert tracker.tracked[100].samples_since_checkpoint == 10  # Not yet


def test_high_band_checkpoints_every_sample():
    """High band checkpoints every sample."""
    from rogue_hunter.tracker import ProcessTracker
    from rogue_hunter.config import BandsConfig

    conn = MagicMock()
    conn.execute.return_value.lastrowid = 1
    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1000)

    score = _make_process_score(pid=100, score=55, band="high")

    # Start tracking
    tracker.update([score])
    initial_calls = conn.execute.call_count

    # Each subsequent update should checkpoint
    tracker.update([score])
    calls_after_1 = conn.execute.call_count
    assert calls_after_1 > initial_calls

    tracker.update([score])
    calls_after_2 = conn.execute.call_count
    assert calls_after_2 > calls_after_1


def test_critical_band_checkpoints_every_sample():
    """Critical band checkpoints every sample."""
    from rogue_hunter.tracker import ProcessTracker
    from rogue_hunter.config import BandsConfig

    conn = MagicMock()
    conn.execute.return_value.lastrowid = 1
    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1000)

    score = _make_process_score(pid=100, score=75, band="critical")

    tracker.update([score])
    initial_calls = conn.execute.call_count

    tracker.update([score])
    assert conn.execute.call_count > initial_calls  # Checkpoint every sample


def _make_process_score(pid: int, score: int, band: str) -> "ProcessScore":
    """Helper to create ProcessScore for testing."""
    from rogue_hunter.collector import ProcessScore
    import time

    return ProcessScore(
        pid=pid,
        command="test",
        captured_at=time.time(),
        cpu_share=1.0,
        gpu_share=0.0,
        mem_share=1.0,
        disk_share=0.0,
        wakeups_share=0.0,
        disproportionality=1.0,
        dominant_resource="cpu",
        cpu=10.0,
        mem=100_000_000,
        mem_peak=200_000_000,
        pageins=0,
        pageins_rate=0.0,
        faults=0,
        faults_rate=0.0,
        disk_io=0,
        disk_io_rate=0.0,
        csw=0,
        csw_rate=0.0,
        syscalls=0,
        syscalls_rate=0.0,
        threads=1,
        mach_msgs=0,
        mach_msgs_rate=0.0,
        instructions=0,
        cycles=0,
        ipc=0.0,
        energy=0,
        energy_rate=0.0,
        wakeups=0,
        wakeups_rate=0.0,
        runnable_time=0,
        runnable_time_rate=0.0,
        qos_interactive=0,
        qos_interactive_rate=0.0,
        gpu_time=0,
        gpu_time_rate=0.0,
        zombie_children=0,
        state="running",
        priority=31,
        score=score,
        band=band,
    )
```

**Step 2: Run test to verify it fails**

```bash
timeout 10s uv run pytest tests/test_tracker.py::test_low_band_not_tracked -v
```
Expected: FAIL (low band processes are currently tracked if above tracking_threshold)

**Step 3: Write minimal implementation**

```python
# src/rogue_hunter/tracker.py

from dataclasses import dataclass

@dataclass
class TrackedProcess:
    """In-memory state for a tracked process."""
    event_id: int
    pid: int
    command: str
    peak_score: int
    peak_band: str
    peak_snapshot_id: int | None
    last_checkpoint: float
    samples_since_checkpoint: int = 0  # NEW: count samples for graduated checkpointing


class ProcessTracker:
    """Manages process event lifecycle with graduated capture."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        bands: BandsConfig,
        boot_time: int,
        on_forensics_trigger: Callable[[int, str], Awaitable[None]] | None = None,
    ):
        self._conn = conn
        self._bands = bands
        self._boot_time = boot_time
        self._on_forensics_trigger = on_forensics_trigger
        self.tracked: dict[int, TrackedProcess] = {}
        self._restore_open_events()

    def update(self, scores: list[ProcessScore]) -> None:
        """Update tracking state based on new scores."""
        seen_pids = set()

        for score in scores:
            seen_pids.add(score.pid)
            band = score.band

            # Low band: never track
            if band == "low":
                continue

            # Determine checkpoint interval based on band
            checkpoint_samples = self._get_checkpoint_samples(band)

            if score.pid in self.tracked:
                tracked = self.tracked[score.pid]
                tracked.samples_since_checkpoint += 1

                # Check if should checkpoint
                should_checkpoint = (
                    checkpoint_samples == 1  # High/Critical: every sample
                    or tracked.samples_since_checkpoint >= checkpoint_samples
                )

                if should_checkpoint:
                    self._insert_checkpoint(score, tracked)
                    tracked.samples_since_checkpoint = 0

                # Update peak if higher
                if score.score > tracked.peak_score:
                    self._update_peak(score)
            else:
                # New process to track
                self._open_event(score)

        # Close events for processes no longer in scores
        for pid in list(self.tracked.keys()):
            if pid not in seen_pids:
                self._close_event(pid, time.time(), None)

    def _get_checkpoint_samples(self, band: str) -> int:
        """Get checkpoint interval in samples for a band."""
        if band in ("high", "critical"):
            return 1  # Every sample
        elif band == "elevated":
            return self._bands.elevated_checkpoint_samples
        elif band == "medium":
            return self._bands.medium_checkpoint_samples
        else:
            return 0  # Low band: no checkpoints (shouldn't reach here)

    def _open_event(self, score: ProcessScore) -> None:
        """Create new event for a process."""
        event_id = create_process_event(
            self._conn,
            pid=score.pid,
            command=score.command,
            boot_time=self._boot_time,
            entry_time=score.captured_at,
            entry_band=score.band,
            peak_score=score.score,
            peak_band=score.band,
        )

        # Insert entry snapshot
        snapshot_id = insert_process_snapshot(
            self._conn, event_id, SNAPSHOT_ENTRY, score
        )

        # Update peak snapshot reference
        update_process_event_peak(
            self._conn,
            event_id,
            peak_score=score.score,
            peak_band=score.band,
            peak_snapshot_id=snapshot_id,
        )

        self.tracked[score.pid] = TrackedProcess(
            event_id=event_id,
            pid=score.pid,
            command=score.command,
            peak_score=score.score,
            peak_band=score.band,
            peak_snapshot_id=snapshot_id,
            last_checkpoint=score.captured_at,
            samples_since_checkpoint=0,
        )

        # Trigger forensics if entering critical band
        if score.band == self._bands.forensics_band and self._on_forensics_trigger:
            import asyncio
            asyncio.create_task(
                self._on_forensics_trigger(event_id, f"band_entry_{score.band}")
            )

    # ... rest of methods (_close_event, _update_peak, _insert_checkpoint, _restore_open_events)
```

**Step 4: Run test to verify it passes**

```bash
timeout 10s uv run pytest tests/test_tracker.py::test_low_band_not_tracked tests/test_tracker.py::test_medium_band_checkpoints_every_n_samples tests/test_tracker.py::test_elevated_band_checkpoints_more_frequently tests/test_tracker.py::test_high_band_checkpoints_every_sample tests/test_tracker.py::test_critical_band_checkpoints_every_sample -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/rogue_hunter/tracker.py tests/test_tracker.py
git commit -m "$(cat <<'EOF'
feat: Implement graduated capture frequency by band

Different bands now have different checkpoint frequencies:
- Low: no tracking
- Medium: every N samples (configurable)
- Elevated: every M samples (configurable, more frequent)
- High/Critical: every sample

Changes:
- Add samples_since_checkpoint to TrackedProcess
- Add _get_checkpoint_samples() method
- Update update() to use sample-based checkpointing
- Low band processes are no longer tracked
EOF
)"
```

---

## Task 11: Fix Forensics Trigger to Use Config

**Context:** Forensics trigger is hardcoded to `("high", "critical")`. Should use `forensics_band` from config, which defaults to "critical".

**Files:**
- Modify: `src/rogue_hunter/tracker.py`
- Test: `tests/test_tracker.py`

**Step 1: Write the failing test**

```python
# tests/test_tracker.py

def test_forensics_only_at_configured_band():
    """Forensics triggers only at forensics_band from config."""
    from rogue_hunter.tracker import ProcessTracker
    from rogue_hunter.config import BandsConfig
    from unittest.mock import MagicMock, AsyncMock

    conn = MagicMock()
    conn.execute.return_value.lastrowid = 1

    # Configure forensics only at critical
    bands = BandsConfig(forensics_band="critical")

    forensics_callback = AsyncMock()
    tracker = ProcessTracker(conn, bands, boot_time=1000, on_forensics_trigger=forensics_callback)

    # High band should NOT trigger forensics
    high_score = _make_process_score(pid=100, score=55, band="high")
    tracker.update([high_score])

    forensics_callback.assert_not_called()

    # Critical band SHOULD trigger forensics
    critical_score = _make_process_score(pid=200, score=75, band="critical")
    tracker.update([critical_score])

    forensics_callback.assert_called_once()


def test_forensics_on_escalation_to_configured_band():
    """Forensics triggers when escalating INTO forensics_band."""
    from rogue_hunter.tracker import ProcessTracker
    from rogue_hunter.config import BandsConfig
    from unittest.mock import MagicMock, AsyncMock

    conn = MagicMock()
    conn.execute.return_value.lastrowid = 1

    bands = BandsConfig(forensics_band="critical")

    forensics_callback = AsyncMock()
    tracker = ProcessTracker(conn, bands, boot_time=1000, on_forensics_trigger=forensics_callback)

    # Start at high band
    high_score = _make_process_score(pid=100, score=55, band="high")
    tracker.update([high_score])

    forensics_callback.assert_not_called()

    # Escalate to critical
    critical_score = _make_process_score(pid=100, score=75, band="critical")
    tracker.update([critical_score])

    # Should trigger on escalation
    forensics_callback.assert_called_once()


def test_forensics_configurable_to_high():
    """Forensics can be configured to trigger at high band."""
    from rogue_hunter.tracker import ProcessTracker
    from rogue_hunter.config import BandsConfig
    from unittest.mock import MagicMock, AsyncMock

    conn = MagicMock()
    conn.execute.return_value.lastrowid = 1

    # Configure forensics at high (not just critical)
    bands = BandsConfig(forensics_band="high")

    forensics_callback = AsyncMock()
    tracker = ProcessTracker(conn, bands, boot_time=1000, on_forensics_trigger=forensics_callback)

    # High band SHOULD trigger forensics now
    high_score = _make_process_score(pid=100, score=55, band="high")
    tracker.update([high_score])

    forensics_callback.assert_called_once()
```

**Step 2: Run test to verify it fails**

```bash
timeout 10s uv run pytest tests/test_tracker.py::test_forensics_only_at_configured_band -v
```
Expected: FAIL (forensics triggers at high because of hardcoded check)

**Step 3: Write minimal implementation**

```python
# src/rogue_hunter/tracker.py

# In _open_event(), replace the hardcoded check:

# OLD (hardcoded):
# if band in ("high", "critical") and self._on_forensics_trigger:

# NEW (uses config):
if self._should_trigger_forensics(score.band) and self._on_forensics_trigger:
    import asyncio
    asyncio.create_task(
        self._on_forensics_trigger(event_id, f"band_entry_{score.band}")
    )


# In _update_peak(), replace the hardcoded check:

# OLD (hardcoded):
# if new_band in ("high", "critical") and old_band not in ("high", "critical"):

# NEW (uses config):
if self._should_trigger_forensics(new_band) and not self._should_trigger_forensics(old_band):
    if self._on_forensics_trigger:
        import asyncio
        asyncio.create_task(
            self._on_forensics_trigger(event_id, f"peak_escalation_{new_band}")
        )


# Add helper method:

def _should_trigger_forensics(self, band: str) -> bool:
    """Check if band should trigger forensics capture."""
    forensics_band = self._bands.forensics_band
    forensics_threshold = self._bands.get_threshold(forensics_band)
    band_threshold = self._bands.get_threshold(band)
    return band_threshold >= forensics_threshold
```

**Step 4: Run test to verify it passes**

```bash
timeout 10s uv run pytest tests/test_tracker.py::test_forensics_only_at_configured_band tests/test_tracker.py::test_forensics_on_escalation_to_configured_band tests/test_tracker.py::test_forensics_configurable_to_high -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/rogue_hunter/tracker.py tests/test_tracker.py
git commit -m "$(cat <<'EOF'
fix: Use forensics_band config instead of hardcoded bands

The forensics trigger was hardcoded to ("high", "critical"), ignoring
the forensics_band setting in config. Now respects the config.

Changes:
- Add _should_trigger_forensics() helper method
- Replace hardcoded band checks in _open_event() and _update_peak()
- Forensics now only triggers at/above configured forensics_band
EOF
)"
```

---

## Task 12: Update TUI for Resource-Based Scoring

**Context:** The TUI displays process scores and needs to show dominant resource instead of dominant category. Update display code to use new ProcessScore fields.

**Files:**
- Modify: `src/rogue_hunter/tui/app.py` (or relevant TUI files)
- Modify: `src/rogue_hunter/tui/widgets/` (if applicable)
- Test: `tests/test_tui.py` (if exists)

**Step 1: Write the failing test**

```python
# tests/test_tui.py

def test_tui_displays_dominant_resource():
    """TUI displays dominant_resource instead of dominant_category."""
    from rogue_hunter.collector import ProcessScore
    import time

    score = ProcessScore(
        pid=123,
        command="test_app",
        captured_at=time.time(),
        cpu_share=5.0,
        gpu_share=0.0,
        mem_share=1.0,
        disk_share=0.0,
        wakeups_share=0.0,
        disproportionality=5.0,
        dominant_resource="cpu",
        cpu=50.0,
        mem=100_000_000,
        mem_peak=200_000_000,
        pageins=0,
        pageins_rate=0.0,
        faults=0,
        faults_rate=0.0,
        disk_io=0,
        disk_io_rate=0.0,
        csw=0,
        csw_rate=0.0,
        syscalls=0,
        syscalls_rate=0.0,
        threads=4,
        mach_msgs=0,
        mach_msgs_rate=0.0,
        instructions=0,
        cycles=0,
        ipc=0.0,
        energy=0,
        energy_rate=0.0,
        wakeups=0,
        wakeups_rate=0.0,
        runnable_time=0,
        runnable_time_rate=0.0,
        qos_interactive=0,
        qos_interactive_rate=0.0,
        gpu_time=0,
        gpu_time_rate=0.0,
        zombie_children=0,
        state="running",
        priority=31,
        score=55,
        band="high",
    )

    # Format for display
    from rogue_hunter.tui.formatters import format_dominant_info

    display = format_dominant_info(score)

    assert "cpu" in display.lower()
    assert "5.0" in display or "5x" in display  # Shows disproportionality


def test_tui_no_category_references():
    """TUI code does not reference old category fields."""
    import ast
    from pathlib import Path

    tui_dir = Path("src/rogue_hunter/tui")
    old_fields = ["dominant_category", "dominant_metrics", "blocking_score",
                  "contention_score", "pressure_score", "efficiency_score"]

    for py_file in tui_dir.rglob("*.py"):
        content = py_file.read_text()
        for field in old_fields:
            assert field not in content, f"Found '{field}' in {py_file}"
```

**Step 2: Run test to verify it fails**

```bash
timeout 10s uv run pytest tests/test_tui.py::test_tui_no_category_references -v
```
Expected: FAIL (TUI still references old fields)

**Step 3: Write minimal implementation**

```python
# src/rogue_hunter/tui/formatters.py (create if doesn't exist, or update existing)

def format_dominant_info(score: "ProcessScore") -> str:
    """Format dominant resource info for display.

    Shows the dominant resource and how disproportionate the usage is.
    """
    resource = score.dominant_resource
    disprop = score.disproportionality

    # Format disproportionality as multiplier
    if disprop >= 100:
        disprop_str = f"{int(disprop)}x"
    elif disprop >= 10:
        disprop_str = f"{disprop:.0f}x"
    elif disprop >= 1:
        disprop_str = f"{disprop:.1f}x"
    else:
        disprop_str = f"{disprop:.2f}x"

    # Emoji/icon for resource type (optional, based on project style)
    resource_labels = {
        "cpu": "CPU",
        "gpu": "GPU",
        "memory": "MEM",
        "disk": "DISK",
        "wakeups": "WAKE",
    }
    label = resource_labels.get(resource, resource.upper())

    return f"{label} {disprop_str}"


# In the main TUI table/display code, update column definitions:

# OLD:
# columns = [..., "Category", "Metrics", ...]
# row_data = [..., score.dominant_category, ", ".join(score.dominant_metrics), ...]

# NEW:
# columns = [..., "Dominant", ...]
# row_data = [..., format_dominant_info(score), ...]


# Update any DataTable or similar widget that displays process info:
# - Remove columns for blocking_score, contention_score, pressure_score, efficiency_score
# - Add or update column for dominant resource display
# - Update column for showing resource shares if desired


# Example update in process table rendering:

def render_process_row(score: ProcessScore) -> list:
    """Render a single process row for the table."""
    return [
        str(score.pid),
        score.command[:20],
        f"{score.score}",
        score.band,
        format_dominant_info(score),
        f"{score.cpu:.1f}%",
        format_bytes(score.mem),
        score.state,
    ]
```

**Step 4: Run test to verify it passes**

```bash
timeout 10s uv run pytest tests/test_tui.py::test_tui_displays_dominant_resource tests/test_tui.py::test_tui_no_category_references -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/rogue_hunter/tui/ tests/test_tui.py
git commit -m "$(cat <<'EOF'
feat: Update TUI for resource-based scoring display

Replace category-based display with dominant resource info.
Shows which resource the process is disproportionately using.

Changes:
- Add format_dominant_info() formatter
- Remove category score columns from process table
- Display dominant resource with disproportionality multiplier
- Remove all references to old category fields
EOF
)"
```

---

## Task 13: Final Cleanup and Memory Updates

**Context:** Ensure all old references are removed, update documentation memories, run linter, verify all tests pass.

**Files:**
- Modify: Various files with stale references
- Update: Serena memories (`data_schema`, `implementation_guide`)
- Run: Linter and tests

**Step 1: Search for stale references**

```bash
# Find any remaining references to old fields
rg -l "blocking_score|contention_score|pressure_score|efficiency_score|dominant_category|dominant_metrics" src/
rg -l "get_core_count" src/
```

**Step 2: Remove stale references**

For each file found:
- Remove imports of deleted functions
- Update any code still referencing old fields
- Fix any type hints or docstrings

**Step 3: Update Serena memories**

```python
# Use mcp__serena__edit_memory to update:

# 1. data_schema memory - update ProcessScore fields
# 2. implementation_guide memory - update scoring documentation

# Example updates:

# data_schema: Replace category score fields with resource share fields
# - Remove: blocking_score, contention_score, pressure_score, efficiency_score
# - Remove: dominant_category, dominant_metrics
# - Add: cpu_share, gpu_share, mem_share, disk_share, wakeups_share
# - Add: disproportionality, dominant_resource

# implementation_guide: Update scoring section
# - Document new disproportionate-share model
# - Document graduated capture behavior
# - Update any references to old 4-category system
```

**Step 4: Run linter and fix issues**

```bash
uv run ruff check . && uv run ruff format .
```

Fix any linter errors before proceeding.

**Step 5: Run full test suite**

```bash
timeout 120s uv run pytest
```

All tests must pass.

**Step 6: Commit cleanup**

```bash
git add -A
git commit -m "$(cat <<'EOF'
chore: Final cleanup for scoring system redesign

Remove all references to old category-based scoring, update documentation.

Changes:
- Remove stale imports and references throughout codebase
- Update data_schema memory with new ProcessScore fields
- Update implementation_guide memory with new scoring model
- Fix all linter errors
- All tests passing
EOF
)"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Add resource weights configuration | config.py |
| 2 | Add sample-based checkpoint configuration | config.py |
| 3 | Update ProcessScore dataclass | collector.py |
| 4 | Update storage schema to v18 | storage.py |
| 5 | Implement active process counting | collector.py |
| 6 | Implement fair share calculation | collector.py |
| 7 | Implement disproportionate-share scoring | collector.py |
| 8 | Integrate new scoring into collector | collector.py |
| 9 | Remove old scoring code | collector.py |
| 10 | Implement graduated capture frequency | tracker.py |
| 11 | Fix forensics trigger to use config | tracker.py |
| 12 | Update TUI for resource-based display | tui/ |
| 13 | Final cleanup and memory updates | various |

**Estimated commits:** 13 (one per task)

**Key files changed:**
- `src/rogue_hunter/config.py` — New config fields
- `src/rogue_hunter/collector.py` — New scoring model
- `src/rogue_hunter/storage.py` — Schema v18
- `src/rogue_hunter/tracker.py` — Graduated capture
- `src/rogue_hunter/tui/` — Display updates

---

*Plan created: 2026-02-01*

