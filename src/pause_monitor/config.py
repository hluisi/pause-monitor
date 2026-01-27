"""Configuration system for pause-monitor."""

from dataclasses import dataclass, field
from pathlib import Path

import tomlkit


@dataclass
class SamplingConfig:
    """Sampling interval configuration."""

    normal_interval: int = 5
    elevated_interval: int = 1


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
class ForensicsConfig:
    """Forensics capture timeout configuration."""

    spindump_timeout: int = 30  # Seconds to wait for spindump
    tailspin_timeout: int = 10  # Seconds to wait for tailspin
    logs_timeout: int = 10  # Seconds to wait for system log capture


@dataclass
class SentinelConfig:
    """Sentinel timing configuration."""

    fast_interval_ms: int = 100
    ring_buffer_seconds: int = 30
    pause_threshold_ratio: float = 2.0  # Latency ratio to detect pause
    peak_tracking_seconds: int = 30  # Interval to update peak stress
    sample_interval_ms: int = 1500  # Expected time for top -l 2 -s 1
    wake_suppress_seconds: float = 10.0  # Suppress pause detection after wake


@dataclass
class BandsConfig:
    """Band thresholds and behavior triggers."""

    low: int = 20
    medium: int = 40
    elevated: int = 60
    high: int = 80
    critical: int = 100
    tracking_band: str = "elevated"
    forensics_band: str = "high"

    def get_band(self, score: int) -> str:
        """Return band name for a given score."""
        if score >= self.high:
            return "critical"
        if score >= self.elevated:
            return "high"
        if score >= self.medium:
            return "elevated"
        if score >= self.low:
            return "medium"
        return "low"

    def get_threshold(self, band: str) -> int:
        """Return the minimum score for a band."""
        thresholds = {
            "low": 0,
            "medium": self.low,
            "elevated": self.medium,
            "high": self.elevated,
            "critical": self.high,
        }
        return thresholds[band]

    @property
    def tracking_threshold(self) -> int:
        """Return the threshold for the tracking band."""
        return self.get_threshold(self.tracking_band)

    @property
    def forensics_threshold(self) -> int:
        """Return the threshold for the forensics band."""
        return self.get_threshold(self.forensics_band)


@dataclass
class ScoringWeights:
    """Weights for per-process stressor scoring (sum to 100, excluding threads)."""

    cpu: int = 25
    state: int = 20
    pageins: int = 15
    mem: int = 15
    cmprs: int = 10
    csw: int = 10
    sysbsd: int = 5
    threads: int = 0


@dataclass
class StateMultipliers:
    """Post-score multipliers based on process state. Applied after base score calculation."""

    idle: float = 0.5
    sleeping: float = 0.6
    stopped: float = 0.7
    halted: float = 0.8
    zombie: float = 0.9
    running: float = 1.0
    stuck: float = 1.0

    def get(self, state: str) -> float:
        """Get multiplier for a state, defaulting to 1.0 for unknown states."""
        return getattr(self, state, 1.0)


@dataclass
class NormalizationConfig:
    """Maximum values for normalizing metrics to 0-1 scale.

    Each value represents what counts as "maxed out" for that metric.
    A process at this value scores 1.0 for that metric component.
    """

    cpu: float = 100.0  # Percentage (natural max)
    mem_gb: float = 8.0  # Memory in gigabytes
    cmprs_gb: float = 1.0  # Compressed memory in gigabytes
    pageins: int = 1000  # Page-ins per sample
    csw: int = 100000  # Context switches per sample
    sysbsd: int = 100000  # Syscalls per sample
    threads: int = 1000  # Thread count


@dataclass
class ScoringConfig:
    """Scoring configuration."""

    weights: ScoringWeights = field(default_factory=ScoringWeights)
    state_multipliers: StateMultipliers = field(default_factory=StateMultipliers)
    normalization: NormalizationConfig = field(default_factory=NormalizationConfig)


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
    states: list[str] = field(default_factory=lambda: ["zombie"])


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
    forensics: ForensicsConfig = field(default_factory=ForensicsConfig)
    sentinel: SentinelConfig = field(default_factory=SentinelConfig)
    bands: BandsConfig = field(default_factory=BandsConfig)
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

        forensics = tomlkit.table()
        forensics.add("spindump_timeout", self.forensics.spindump_timeout)
        forensics.add("tailspin_timeout", self.forensics.tailspin_timeout)
        forensics.add("logs_timeout", self.forensics.logs_timeout)
        doc.add("forensics", forensics)
        doc.add(tomlkit.nl())

        sentinel = tomlkit.table()
        sentinel.add("fast_interval_ms", self.sentinel.fast_interval_ms)
        sentinel.add("ring_buffer_seconds", self.sentinel.ring_buffer_seconds)
        sentinel.add("pause_threshold_ratio", self.sentinel.pause_threshold_ratio)
        sentinel.add("peak_tracking_seconds", self.sentinel.peak_tracking_seconds)
        sentinel.add("sample_interval_ms", self.sentinel.sample_interval_ms)
        sentinel.add("wake_suppress_seconds", self.sentinel.wake_suppress_seconds)
        doc.add("sentinel", sentinel)
        doc.add(tomlkit.nl())

        bands = tomlkit.table()
        bands.add("low", self.bands.low)
        bands.add("medium", self.bands.medium)
        bands.add("elevated", self.bands.elevated)
        bands.add("high", self.bands.high)
        bands.add("critical", self.bands.critical)
        bands.add("tracking_band", self.bands.tracking_band)
        bands.add("forensics_band", self.bands.forensics_band)
        doc.add("bands", bands)
        doc.add(tomlkit.nl())

        # Scoring section with nested weights and state multipliers
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

        state_mult = tomlkit.table()
        state_mult.add("idle", self.scoring.state_multipliers.idle)
        state_mult.add("sleeping", self.scoring.state_multipliers.sleeping)
        state_mult.add("stopped", self.scoring.state_multipliers.stopped)
        state_mult.add("halted", self.scoring.state_multipliers.halted)
        state_mult.add("zombie", self.scoring.state_multipliers.zombie)
        state_mult.add("running", self.scoring.state_multipliers.running)
        state_mult.add("stuck", self.scoring.state_multipliers.stuck)
        scoring.add("state_multipliers", state_mult)

        norm = tomlkit.table()
        norm.add("cpu", self.scoring.normalization.cpu)
        norm.add("mem_gb", self.scoring.normalization.mem_gb)
        norm.add("cmprs_gb", self.scoring.normalization.cmprs_gb)
        norm.add("pageins", self.scoring.normalization.pageins)
        norm.add("csw", self.scoring.normalization.csw)
        norm.add("sysbsd", self.scoring.normalization.sysbsd)
        norm.add("threads", self.scoring.normalization.threads)
        scoring.add("normalization", norm)

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
        forensics_data = data.get("forensics", {})
        sentinel_data = data.get("sentinel", {})
        bands_data = data.get("bands", {})
        scoring_data = data.get("scoring", {})
        rogue_data = data.get("rogue_selection", {})

        return cls(
            sampling=SamplingConfig(
                normal_interval=sampling_data.get("normal_interval", 5),
                elevated_interval=sampling_data.get("elevated_interval", 1),
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
            forensics=ForensicsConfig(
                spindump_timeout=forensics_data.get("spindump_timeout", 30),
                tailspin_timeout=forensics_data.get("tailspin_timeout", 10),
                logs_timeout=forensics_data.get("logs_timeout", 10),
            ),
            sentinel=SentinelConfig(
                fast_interval_ms=sentinel_data.get("fast_interval_ms", 100),
                ring_buffer_seconds=sentinel_data.get("ring_buffer_seconds", 30),
                pause_threshold_ratio=sentinel_data.get("pause_threshold_ratio", 2.0),
                peak_tracking_seconds=sentinel_data.get("peak_tracking_seconds", 30),
                sample_interval_ms=sentinel_data.get("sample_interval_ms", 1500),
                wake_suppress_seconds=sentinel_data.get("wake_suppress_seconds", 10.0),
            ),
            bands=_load_bands_config(bands_data),
            scoring=_load_scoring_config(scoring_data),
            rogue_selection=_load_rogue_selection_config(rogue_data),
            learning_mode=data.get("learning_mode", False),
        )


def _load_bands_config(data: dict) -> BandsConfig:
    """Load bands config from TOML data, using dataclass defaults for missing fields."""
    defaults = BandsConfig()
    return BandsConfig(
        low=data.get("low", defaults.low),
        medium=data.get("medium", defaults.medium),
        elevated=data.get("elevated", defaults.elevated),
        high=data.get("high", defaults.high),
        critical=data.get("critical", defaults.critical),
        tracking_band=data.get("tracking_band", defaults.tracking_band),
        forensics_band=data.get("forensics_band", defaults.forensics_band),
    )


def _load_scoring_config(data: dict) -> ScoringConfig:
    """Load scoring config from TOML data."""
    weights_data = data.get("weights", {})
    state_mult_data = data.get("state_multipliers", {})
    norm_data = data.get("normalization", {})

    defaults = NormalizationConfig()
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
        ),
        state_multipliers=StateMultipliers(
            idle=state_mult_data.get("idle", 0.5),
            sleeping=state_mult_data.get("sleeping", 0.6),
            stopped=state_mult_data.get("stopped", 0.7),
            halted=state_mult_data.get("halted", 0.8),
            zombie=state_mult_data.get("zombie", 0.9),
            running=state_mult_data.get("running", 1.0),
            stuck=state_mult_data.get("stuck", 1.0),
        ),
        normalization=NormalizationConfig(
            cpu=norm_data.get("cpu", defaults.cpu),
            mem_gb=norm_data.get("mem_gb", defaults.mem_gb),
            cmprs_gb=norm_data.get("cmprs_gb", defaults.cmprs_gb),
            pageins=norm_data.get("pageins", defaults.pageins),
            csw=norm_data.get("csw", defaults.csw),
            sysbsd=norm_data.get("sysbsd", defaults.sysbsd),
            threads=norm_data.get("threads", defaults.threads),
        ),
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
        states=data.get("states", ["zombie"]),
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
