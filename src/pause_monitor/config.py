"""Configuration system for pause-monitor."""

from dataclasses import dataclass, field
from pathlib import Path

import tomlkit


@dataclass
class SamplingConfig:
    """Sampling interval configuration."""

    normal_interval: int = 5
    elevated_interval: int = 1
    elevation_threshold: int = 30
    critical_threshold: int = 60


@dataclass
class RetentionConfig:
    """Data retention configuration."""

    samples_days: int = 30
    events_days: int = 90


@dataclass
class AlertsConfig:
    """Alert notification configuration."""

    enabled: bool = True
    pause_detected: bool = True
    pause_min_duration: float = 2.0
    critical_stress: bool = True
    critical_threshold: int = 60
    critical_duration: int = 30
    elevated_entered: bool = False
    forensics_completed: bool = True
    sound: bool = True


@dataclass
class SuspectsConfig:
    """Process suspect pattern configuration."""

    patterns: list[str] = field(
        default_factory=lambda: [
            "codemeter",
            "bitdefender",
            "biomesyncd",
            "motu",
            "coreaudiod",
            "kernel_task",
            "WindowServer",
        ]
    )


@dataclass
class SentinelConfig:
    """Sentinel timing configuration."""

    fast_interval_ms: int = 100
    ring_buffer_seconds: int = 30
    pause_threshold_ratio: float = 2.0  # Latency ratio to detect pause
    peak_tracking_seconds: int = 30  # Interval to update peak stress


@dataclass
class TiersConfig:
    """Tier threshold configuration for process scores."""

    elevated_threshold: int = 35
    critical_threshold: int = 65


@dataclass
class ScoringWeights:
    """Weights for per-process stressor scoring (default weights sum to 100, excluding threads)."""

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


@dataclass
class Config:
    """Main configuration container."""

    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    suspects: SuspectsConfig = field(default_factory=SuspectsConfig)
    sentinel: SentinelConfig = field(default_factory=SentinelConfig)
    tiers: TiersConfig = field(default_factory=TiersConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    rogue_selection: RogueSelectionConfig = field(default_factory=RogueSelectionConfig)
    learning_mode: bool = False

    @property
    def config_dir(self) -> Path:
        """Configuration directory."""
        return Path.home() / ".config" / "pause-monitor"

    @property
    def config_path(self) -> Path:
        """Path to config file."""
        return self.config_dir / "config.toml"

    @property
    def data_dir(self) -> Path:
        """Data directory."""
        return Path.home() / ".local" / "share" / "pause-monitor"

    @property
    def events_dir(self) -> Path:
        """Events directory for forensics."""
        return self.data_dir / "events"

    @property
    def db_path(self) -> Path:
        """Database path."""
        return self.data_dir / "data.db"

    @property
    def log_path(self) -> Path:
        """Daemon log path."""
        return self.data_dir / "daemon.log"

    @property
    def pid_path(self) -> Path:
        """PID file path."""
        return self.data_dir / "daemon.pid"

    @property
    def socket_path(self) -> Path:
        """Unix socket path for daemon IPC."""
        return self.data_dir / "daemon.sock"

    def save(self, path: Path | None = None) -> None:
        """Save config to TOML file."""
        path = path or self.config_path
        path.parent.mkdir(parents=True, exist_ok=True)

        doc = tomlkit.document()
        doc.add("learning_mode", self.learning_mode)
        doc.add(tomlkit.nl())

        sampling = tomlkit.table()
        sampling.add("normal_interval", self.sampling.normal_interval)
        sampling.add("elevated_interval", self.sampling.elevated_interval)
        sampling.add("elevation_threshold", self.sampling.elevation_threshold)
        sampling.add("critical_threshold", self.sampling.critical_threshold)
        doc.add("sampling", sampling)
        doc.add(tomlkit.nl())

        retention = tomlkit.table()
        retention.add("samples_days", self.retention.samples_days)
        retention.add("events_days", self.retention.events_days)
        doc.add("retention", retention)
        doc.add(tomlkit.nl())

        alerts = tomlkit.table()
        alerts.add("enabled", self.alerts.enabled)
        alerts.add("pause_detected", self.alerts.pause_detected)
        alerts.add("pause_min_duration", self.alerts.pause_min_duration)
        alerts.add("critical_stress", self.alerts.critical_stress)
        alerts.add("critical_threshold", self.alerts.critical_threshold)
        alerts.add("critical_duration", self.alerts.critical_duration)
        alerts.add("elevated_entered", self.alerts.elevated_entered)
        alerts.add("forensics_completed", self.alerts.forensics_completed)
        alerts.add("sound", self.alerts.sound)
        doc.add("alerts", alerts)
        doc.add(tomlkit.nl())

        suspects = tomlkit.table()
        suspects.add("patterns", self.suspects.patterns)
        doc.add("suspects", suspects)
        doc.add(tomlkit.nl())

        sentinel = tomlkit.table()
        sentinel.add("fast_interval_ms", self.sentinel.fast_interval_ms)
        sentinel.add("ring_buffer_seconds", self.sentinel.ring_buffer_seconds)
        sentinel.add("pause_threshold_ratio", self.sentinel.pause_threshold_ratio)
        sentinel.add("peak_tracking_seconds", self.sentinel.peak_tracking_seconds)
        doc.add("sentinel", sentinel)
        doc.add(tomlkit.nl())

        tiers = tomlkit.table()
        tiers.add("elevated_threshold", self.tiers.elevated_threshold)
        tiers.add("critical_threshold", self.tiers.critical_threshold)
        doc.add("tiers", tiers)
        doc.add(tomlkit.nl())

        # Scoring section with nested weights
        scoring = tomlkit.table()
        weights = tomlkit.table()
        weights.add("cpu", self.scoring.weights.cpu)
        weights.add("state", self.scoring.weights.state)
        weights.add("pageins", self.scoring.weights.pageins)
        weights.add("mem", self.scoring.weights.mem)
        weights.add("cmprs", self.scoring.weights.cmprs)
        weights.add("csw", self.scoring.weights.csw)
        weights.add("sysbsd", self.scoring.weights.sysbsd)
        weights.add("threads", self.scoring.weights.threads)
        scoring.add("weights", weights)
        doc.add("scoring", scoring)
        doc.add(tomlkit.nl())

        # Rogue selection section with nested category configs
        rogue = tomlkit.table()

        cpu_sel = tomlkit.table()
        cpu_sel.add("enabled", self.rogue_selection.cpu.enabled)
        cpu_sel.add("count", self.rogue_selection.cpu.count)
        cpu_sel.add("threshold", self.rogue_selection.cpu.threshold)
        rogue.add("cpu", cpu_sel)

        mem_sel = tomlkit.table()
        mem_sel.add("enabled", self.rogue_selection.mem.enabled)
        mem_sel.add("count", self.rogue_selection.mem.count)
        mem_sel.add("threshold", self.rogue_selection.mem.threshold)
        rogue.add("mem", mem_sel)

        cmprs_sel = tomlkit.table()
        cmprs_sel.add("enabled", self.rogue_selection.cmprs.enabled)
        cmprs_sel.add("count", self.rogue_selection.cmprs.count)
        cmprs_sel.add("threshold", self.rogue_selection.cmprs.threshold)
        rogue.add("cmprs", cmprs_sel)

        threads_sel = tomlkit.table()
        threads_sel.add("enabled", self.rogue_selection.threads.enabled)
        threads_sel.add("count", self.rogue_selection.threads.count)
        threads_sel.add("threshold", self.rogue_selection.threads.threshold)
        rogue.add("threads", threads_sel)

        csw_sel = tomlkit.table()
        csw_sel.add("enabled", self.rogue_selection.csw.enabled)
        csw_sel.add("count", self.rogue_selection.csw.count)
        csw_sel.add("threshold", self.rogue_selection.csw.threshold)
        rogue.add("csw", csw_sel)

        sysbsd_sel = tomlkit.table()
        sysbsd_sel.add("enabled", self.rogue_selection.sysbsd.enabled)
        sysbsd_sel.add("count", self.rogue_selection.sysbsd.count)
        sysbsd_sel.add("threshold", self.rogue_selection.sysbsd.threshold)
        rogue.add("sysbsd", sysbsd_sel)

        pageins_sel = tomlkit.table()
        pageins_sel.add("enabled", self.rogue_selection.pageins.enabled)
        pageins_sel.add("count", self.rogue_selection.pageins.count)
        pageins_sel.add("threshold", self.rogue_selection.pageins.threshold)
        rogue.add("pageins", pageins_sel)

        state_sel = tomlkit.table()
        state_sel.add("enabled", self.rogue_selection.state.enabled)
        state_sel.add("count", self.rogue_selection.state.count)
        state_sel.add("states", self.rogue_selection.state.states)
        rogue.add("state", state_sel)

        doc.add("rogue_selection", rogue)

        path.write_text(tomlkit.dumps(doc))

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """Load config from TOML file, returning defaults for missing values."""
        config = cls()
        path = path or config.config_path
        if not path.exists():
            return config

        with open(path) as f:
            data = tomlkit.load(f)

        sampling_data = data.get("sampling", {})
        retention_data = data.get("retention", {})
        alerts_data = data.get("alerts", {})
        suspects_data = data.get("suspects", {})
        sentinel_data = data.get("sentinel", {})
        tiers_data = data.get("tiers", {})
        scoring_data = data.get("scoring", {})
        rogue_data = data.get("rogue_selection", {})

        return cls(
            sampling=SamplingConfig(
                normal_interval=sampling_data.get("normal_interval", 5),
                elevated_interval=sampling_data.get("elevated_interval", 1),
                elevation_threshold=sampling_data.get("elevation_threshold", 30),
                critical_threshold=sampling_data.get("critical_threshold", 60),
            ),
            retention=RetentionConfig(
                samples_days=retention_data.get("samples_days", 30),
                events_days=retention_data.get("events_days", 90),
            ),
            alerts=AlertsConfig(
                enabled=alerts_data.get("enabled", True),
                pause_detected=alerts_data.get("pause_detected", True),
                pause_min_duration=alerts_data.get("pause_min_duration", 2.0),
                critical_stress=alerts_data.get("critical_stress", True),
                critical_threshold=alerts_data.get("critical_threshold", 60),
                critical_duration=alerts_data.get("critical_duration", 30),
                elevated_entered=alerts_data.get("elevated_entered", False),
                forensics_completed=alerts_data.get("forensics_completed", True),
                sound=alerts_data.get("sound", True),
            ),
            suspects=SuspectsConfig(
                patterns=suspects_data.get(
                    "patterns",
                    [
                        "codemeter",
                        "bitdefender",
                        "biomesyncd",
                        "motu",
                        "coreaudiod",
                        "kernel_task",
                        "WindowServer",
                    ],
                ),
            ),
            sentinel=SentinelConfig(
                fast_interval_ms=sentinel_data.get("fast_interval_ms", 100),
                ring_buffer_seconds=sentinel_data.get("ring_buffer_seconds", 30),
                pause_threshold_ratio=sentinel_data.get("pause_threshold_ratio", 2.0),
                peak_tracking_seconds=sentinel_data.get("peak_tracking_seconds", 30),
            ),
            tiers=TiersConfig(
                elevated_threshold=tiers_data.get("elevated_threshold", 35),
                critical_threshold=tiers_data.get("critical_threshold", 65),
            ),
            scoring=_load_scoring_config(scoring_data),
            rogue_selection=_load_rogue_selection_config(rogue_data),
            learning_mode=data.get("learning_mode", False),
        )


def _load_scoring_config(data: dict) -> ScoringConfig:
    """Load scoring config from TOML data."""
    weights_data = data.get("weights", {})
    return ScoringConfig(
        weights=ScoringWeights(
            cpu=weights_data.get("cpu", 25),
            state=weights_data.get("state", 20),
            pageins=weights_data.get("pageins", 15),
            mem=weights_data.get("mem", 15),
            cmprs=weights_data.get("cmprs", 10),
            csw=weights_data.get("csw", 10),
            sysbsd=weights_data.get("sysbsd", 5),
            threads=weights_data.get("threads", 0),
        )
    )


def _load_category_selection(data: dict) -> CategorySelection:
    """Load category selection from TOML data."""
    return CategorySelection(
        enabled=data.get("enabled", True),
        count=data.get("count", 3),
        threshold=data.get("threshold", 0.0),
    )


def _load_state_selection(data: dict) -> StateSelection:
    """Load state selection from TOML data."""
    return StateSelection(
        enabled=data.get("enabled", True),
        count=data.get("count", 0),
        states=data.get("states", ["stuck", "zombie"]),
    )


def _load_rogue_selection_config(data: dict) -> RogueSelectionConfig:
    """Load rogue selection config from TOML data."""
    return RogueSelectionConfig(
        cpu=_load_category_selection(data.get("cpu", {})),
        mem=_load_category_selection(data.get("mem", {})),
        cmprs=_load_category_selection(data.get("cmprs", {})),
        threads=_load_category_selection(data.get("threads", {})),
        csw=_load_category_selection(data.get("csw", {})),
        sysbsd=_load_category_selection(data.get("sysbsd", {})),
        pageins=_load_category_selection(data.get("pageins", {})),
        state=_load_state_selection(data.get("state", {})),
    )
