"""Configuration system for pause-monitor."""

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path

import tomlkit


@dataclass
class RetentionConfig:
    """Data retention configuration."""

    events_days: int = 90


@dataclass
class SentinelConfig:
    """Ring buffer configuration."""

    ring_buffer_seconds: int = 60  # Seconds of samples to keep in ring buffer


@dataclass
class BandsConfig:
    """Band thresholds and behavior triggers.

    Each threshold is the minimum score to enter that band.
    Scores below `medium` are in the "low" band (not tracked).
    """

    medium: int = 20  # Score to enter "medium" band
    elevated: int = 40  # Score to enter "elevated" band
    high: int = 50  # Score to enter "high" band
    critical: int = 70  # Score to enter "critical" band
    tracking_band: str = "elevated"
    forensics_band: str = "high"
    checkpoint_interval: int = 30  # Seconds between checkpoint snapshots while tracking

    def get_band(self, score: int) -> str:
        """Return band name for a given score."""
        if score >= self.critical:
            return "critical"
        if score >= self.high:
            return "high"
        if score >= self.elevated:
            return "elevated"
        if score >= self.medium:
            return "medium"
        return "low"

    def get_threshold(self, band: str) -> int:
        """Return the minimum score for a band."""
        thresholds = {
            "low": 0,
            "medium": self.medium,
            "elevated": self.elevated,
            "high": self.high,
            "critical": self.critical,
        }
        if band not in thresholds:
            raise ValueError(f"Unknown band: {band!r}. Valid bands: {list(thresholds.keys())}")
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
    state: int = 15
    pageins: int = 15
    mem: int = 15
    cmprs: int = 10
    csw: int = 10
    sysbsd: int = 5
    threads: int = 5


@dataclass
class StateMultipliers:
    """Post-score multipliers based on process state. Applied after base score calculation."""

    idle: float = 0.5
    sleeping: float = 0.5
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


def _dataclass_to_table(obj: object) -> tomlkit.items.Table:
    """Convert a dataclass instance to a tomlkit Table recursively."""
    table = tomlkit.table()
    for f in fields(obj):  # type: ignore[arg-type]
        value = getattr(obj, f.name)
        if is_dataclass(value) and not isinstance(value, type):
            table.add(f.name, _dataclass_to_table(value))
        else:
            table.add(f.name, value)
    return table


@dataclass
class Config:
    """Main configuration container."""

    retention: RetentionConfig = field(default_factory=RetentionConfig)
    sentinel: SentinelConfig = field(default_factory=SentinelConfig)
    bands: BandsConfig = field(default_factory=BandsConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    rogue_selection: RogueSelectionConfig = field(default_factory=RogueSelectionConfig)

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
        sections = [
            "retention",
            "sentinel",
            "bands",
            "scoring",
            "rogue_selection",
        ]
        for name in sections:
            doc.add(name, _dataclass_to_table(getattr(self, name)))
            doc.add(tomlkit.nl())

        path.write_text(tomlkit.dumps(doc))

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """Load config from TOML file, returning defaults for missing values.

        All defaults come from the dataclass definitions - no hardcoded values here.
        This ensures Config() and Config.load() use identical defaults.
        """
        defaults = cls()
        path = path or defaults.config_path
        if not path.exists():
            return defaults

        try:
            with open(path) as f:
                data = tomlkit.load(f)
        except tomlkit.exceptions.TOMLKitError as e:
            raise ValueError(f"Failed to parse config file {path}: {e}") from e

        retention_data = data.get("retention", {})
        sentinel_data = data.get("sentinel", {})
        bands_data = data.get("bands", {})
        scoring_data = data.get("scoring", {})
        rogue_data = data.get("rogue_selection", {})

        # Use dataclass defaults for any missing values
        ret_defaults = defaults.retention
        sen_defaults = defaults.sentinel

        return cls(
            retention=RetentionConfig(
                events_days=retention_data.get("events_days", ret_defaults.events_days),
            ),
            sentinel=SentinelConfig(
                ring_buffer_seconds=sentinel_data.get(
                    "ring_buffer_seconds", sen_defaults.ring_buffer_seconds
                ),
            ),
            bands=_load_bands_config(bands_data),
            scoring=_load_scoring_config(scoring_data),
            rogue_selection=_load_rogue_selection_config(rogue_data),
        )


def _load_bands_config(data: dict) -> BandsConfig:
    """Load bands config from TOML data, using dataclass defaults for missing fields."""
    defaults = BandsConfig()
    valid_bands = {"low", "medium", "elevated", "high", "critical"}

    tracking_band = data.get("tracking_band", defaults.tracking_band)
    forensics_band = data.get("forensics_band", defaults.forensics_band)

    if tracking_band not in valid_bands:
        raise ValueError(f"Invalid tracking_band: {tracking_band!r}. Must be one of {valid_bands}")
    if forensics_band not in valid_bands:
        raise ValueError(
            f"Invalid forensics_band: {forensics_band!r}. Must be one of {valid_bands}"
        )

    return BandsConfig(
        medium=data.get("medium", defaults.medium),
        elevated=data.get("elevated", defaults.elevated),
        high=data.get("high", defaults.high),
        critical=data.get("critical", defaults.critical),
        tracking_band=tracking_band,
        forensics_band=forensics_band,
    )


def _load_scoring_config(data: dict) -> ScoringConfig:
    """Load scoring config from TOML data."""
    weights_data = data.get("weights", {})
    state_mult_data = data.get("state_multipliers", {})
    norm_data = data.get("normalization", {})

    # Use dataclass instances as single source of truth for defaults
    w = ScoringWeights()
    m = StateMultipliers()
    n = NormalizationConfig()

    return ScoringConfig(
        weights=ScoringWeights(
            cpu=weights_data.get("cpu", w.cpu),
            state=weights_data.get("state", w.state),
            pageins=weights_data.get("pageins", w.pageins),
            mem=weights_data.get("mem", w.mem),
            cmprs=weights_data.get("cmprs", w.cmprs),
            csw=weights_data.get("csw", w.csw),
            sysbsd=weights_data.get("sysbsd", w.sysbsd),
            threads=weights_data.get("threads", w.threads),
        ),
        state_multipliers=StateMultipliers(
            idle=state_mult_data.get("idle", m.idle),
            sleeping=state_mult_data.get("sleeping", m.sleeping),
            stopped=state_mult_data.get("stopped", m.stopped),
            halted=state_mult_data.get("halted", m.halted),
            zombie=state_mult_data.get("zombie", m.zombie),
            running=state_mult_data.get("running", m.running),
            stuck=state_mult_data.get("stuck", m.stuck),
        ),
        normalization=NormalizationConfig(
            cpu=norm_data.get("cpu", n.cpu),
            mem_gb=norm_data.get("mem_gb", n.mem_gb),
            cmprs_gb=norm_data.get("cmprs_gb", n.cmprs_gb),
            pageins=norm_data.get("pageins", n.pageins),
            csw=norm_data.get("csw", n.csw),
            sysbsd=norm_data.get("sysbsd", n.sysbsd),
            threads=norm_data.get("threads", n.threads),
        ),
    )


def _load_category_selection(data: dict) -> CategorySelection:
    """Load category selection from TOML data."""
    d = CategorySelection()
    return CategorySelection(
        enabled=data.get("enabled", d.enabled),
        count=data.get("count", d.count),
        threshold=data.get("threshold", d.threshold),
    )


def _load_state_selection(data: dict) -> StateSelection:
    """Load state selection from TOML data."""
    d = StateSelection()
    return StateSelection(
        enabled=data.get("enabled", d.enabled),
        count=data.get("count", d.count),
        states=data.get("states", d.states),
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
