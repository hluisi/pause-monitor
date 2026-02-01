"""Configuration system for rogue-hunter."""

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path

import tomlkit


@dataclass
class RetentionConfig:
    """Data retention configuration."""

    events_days: int = 90


@dataclass
class SystemConfig:
    """System monitoring configuration."""

    ring_buffer_size: int = 60  # Number of samples to keep in ring buffer
    sample_interval: float = 1 / 3  # Seconds between samples (~0.333s = 3Hz)
    forensics_debounce: float = 2.0  # Min seconds between forensics captures


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
class StateMultipliers:
    """Post-score multipliers based on process state. Applied after base score calculation.

    Multiplier reasoning:
    - running/stuck (1.0): Actively executing, full weight
    - sleeping (0.75): May be I/O-bound; sleeping WITH high disk_io_rate is significant
    - idle (0.3): Brief transitional state during process creation
    - stopped (0.2): Frozen (SIGSTOP/debugger), cannot execute until resumed
    - zombie (0.0): Dead. Metrics are stale history. Cannot cause problems.
    """

    idle: float = 0.3
    sleeping: float = 0.75
    stopped: float = 0.2
    zombie: float = 0.0
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

    Rate thresholds are per-second values, NOT cumulative totals.
    """

    # Basic metrics
    cpu: float = 100.0  # CPU percentage (natural max)
    mem_gb: float = 8.0  # Memory in gigabytes

    # Rate thresholds (per second) - used for 4-category scoring
    pageins_rate: float = 100.0  # 100 page-ins/sec = serious thrashing
    faults_rate: float = 10_000.0  # 10k faults/sec
    csw_rate: float = 10_000.0  # 10k context switches/sec
    syscalls_rate: float = 100_000.0  # 100k syscalls/sec
    mach_msgs_rate: float = 10_000.0  # 10k mach messages/sec
    wakeups_rate: float = 1_000.0  # 1k wakeups/sec
    disk_io_rate: float = 100_000_000  # 100 MB/s

    # Contention thresholds
    runnable_time_rate: float = 100.0  # 100ms runnable per second (10% contention)
    qos_interactive_rate: float = 100.0  # 100ms interactive QoS per second

    # GPU thresholds
    gpu_time_rate: float = 500.0  # 500ms GPU/sec = significant GPU load

    # Efficiency thresholds
    threads: int = 100  # 100 threads is already excessive
    ipc_min: float = 0.5  # IPC below this is concerning (inverse scoring)

    # Pressure thresholds
    zombie_children: int = 10  # 10+ unreaped zombies = significant problem


@dataclass
class ScoringConfig:
    """Scoring configuration.

    Uses 4-category scoring (blocking, contention, pressure, efficiency).
    Weights are hardcoded: 40%, 30%, 20%, 10%.
    """

    state_multipliers: StateMultipliers = field(default_factory=StateMultipliers)
    normalization: NormalizationConfig = field(default_factory=NormalizationConfig)


@dataclass
class RogueSelectionConfig:
    """Configuration for rogue process selection.

    Simple threshold-based selection:
    - Processes with score >= score_threshold are included
    - Stuck processes are always included
    - Results limited to max_count
    """

    score_threshold: int = 20  # Minimum score to be considered a rogue
    max_count: int = 20  # Maximum rogues to track


# =============================================================================
# TUI Color Configuration
# =============================================================================


@dataclass
class BandColors:
    """Colors for score band visualization.

    Colors can be:
    - Named colors: "red", "green", "yellow", "dim"
    - Hex colors: "#FFA500" (orange)
    - Rich styles: "bold red", "dim green"
    - Empty string "" for default text color

    Default palette: Dracula theme - provides visual hierarchy through
    severity progression while maintaining harmony.
    """

    low: str = "#50fa7b"  # Dracula green - healthy, all good
    medium: str = "#8be9fd"  # Dracula cyan - normal operation
    elevated: str = "#f1fa8c"  # Dracula yellow - attention needed
    high: str = "#ffb86c"  # Dracula orange - warning
    critical: str = "#ff5555"  # Dracula red - urgent


@dataclass
class TrendColors:
    """Colors for trend indicators (▲▽●○).

    - worsening: Score increasing (bad) - ▲
    - improving: Score decreasing (good) - ▽
    - stable: No change - ● (inherits row color if empty)
    - decayed: Left rogues list - ○

    Default palette: Dracula theme.
    Worsening inherits row color (severity already visible).
    Improving gets green for positive feedback.
    """

    worsening: str = ""  # Inherit row color - severity already shown
    improving: str = "#50fa7b"  # Dracula green - positive feedback
    stable: str = ""  # Inherit row color
    decayed: str = "dim"


@dataclass
class CategoryColors:
    """Colors for category columns (Blk/Ctn/Prs/Eff).

    Default palette: Dracula theme - each category gets a distinct color
    from the same palette for visual separation while maintaining harmony.
    Empty string "" means inherit row color.
    """

    blocking: str = "#ff5555"  # Dracula red - most severe category
    contention: str = "#ffb86c"  # Dracula orange - resource competition
    pressure: str = "#f1fa8c"  # Dracula yellow - system pressure
    efficiency: str = "#bd93f9"  # Dracula purple - efficiency issues


@dataclass
class StatusColors:
    """Colors for tracked panel status column.

    Default palette: Dracula theme.
    """

    active: str = "#50fa7b"  # Dracula green - active/alive
    ended: str = "dim"


@dataclass
class BorderColors:
    """Colors for panel borders by state.

    Used for HeaderBar border coloring based on system stress level.
    Default palette: Dracula theme.
    """

    normal: str = "#50fa7b"  # Dracula green - system healthy
    elevated: str = "#f1fa8c"  # Dracula yellow - attention
    critical: str = "#ff5555"  # Dracula red - urgent
    disconnected: str = "#ff5555"  # Same as critical


@dataclass
class TUIColorsConfig:
    """All TUI color configurations grouped together."""

    bands: BandColors = field(default_factory=BandColors)
    trends: TrendColors = field(default_factory=TrendColors)
    categories: CategoryColors = field(default_factory=CategoryColors)
    status: StatusColors = field(default_factory=StatusColors)
    borders: BorderColors = field(default_factory=BorderColors)


@dataclass
class TUIConfig:
    """TUI-specific configuration."""

    colors: TUIColorsConfig = field(default_factory=TUIColorsConfig)


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
    system: SystemConfig = field(default_factory=SystemConfig)
    bands: BandsConfig = field(default_factory=BandsConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    rogue_selection: RogueSelectionConfig = field(default_factory=RogueSelectionConfig)
    tui: TUIConfig = field(default_factory=TUIConfig)

    @property
    def config_dir(self) -> Path:
        """Configuration directory."""
        return Path.home() / ".config" / "rogue-hunter"

    @property
    def config_path(self) -> Path:
        """Path to config file."""
        return self.config_dir / "config.toml"

    @property
    def data_dir(self) -> Path:
        """Data directory."""
        return Path.home() / ".local" / "share" / "rogue-hunter"

    @property
    def state_dir(self) -> Path:
        """State directory for logs and other expendable persistent state."""
        return Path.home() / ".local" / "state" / "rogue-hunter"

    @property
    def runtime_dir(self) -> Path:
        """Runtime directory for ephemeral files (PID, socket, tailspin captures).

        Stored in /tmp/ so it's cleared on reboot, avoiding stale file issues.
        This path is also used in the sudoers rule for tailspin permissions.
        """
        return Path("/tmp/rogue-hunter")

    @property
    def db_path(self) -> Path:
        """Database path."""
        return self.data_dir / "data.db"

    @property
    def log_path(self) -> Path:
        """Daemon log path.

        Logs are expendable persistent state, so they go in XDG_STATE_HOME.
        """
        return self.state_dir / "daemon.log"

    @property
    def pid_path(self) -> Path:
        """PID file path."""
        return self.runtime_dir / "daemon.pid"

    @property
    def socket_path(self) -> Path:
        """Unix socket path for daemon IPC."""
        return self.runtime_dir / "daemon.sock"

    def save(self, path: Path | None = None) -> None:
        """Save config to TOML file."""
        path = path or self.config_path
        path.parent.mkdir(parents=True, exist_ok=True)

        doc = tomlkit.document()
        sections = [
            "retention",
            "system",
            "bands",
            "scoring",
            "rogue_selection",
            "tui",
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
        system_data = data.get("system", {})
        bands_data = data.get("bands", {})
        scoring_data = data.get("scoring", {})
        rogue_data = data.get("rogue_selection", {})
        tui_data = data.get("tui", {})

        # Use dataclass defaults for any missing values
        ret_defaults = defaults.retention
        sys_defaults = defaults.system

        return cls(
            retention=RetentionConfig(
                events_days=retention_data.get("events_days", ret_defaults.events_days),
            ),
            system=SystemConfig(
                ring_buffer_size=system_data.get("ring_buffer_size", sys_defaults.ring_buffer_size),
                sample_interval=system_data.get("sample_interval", sys_defaults.sample_interval),
                forensics_debounce=system_data.get(
                    "forensics_debounce", sys_defaults.forensics_debounce
                ),
            ),
            bands=_load_bands_config(bands_data),
            scoring=_load_scoring_config(scoring_data),
            rogue_selection=_load_rogue_selection_config(rogue_data),
            tui=_load_tui_config(tui_data),
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
    state_mult_data = data.get("state_multipliers", {})
    norm_data = data.get("normalization", {})

    # Use dataclass instances as single source of truth for defaults
    m = StateMultipliers()
    n = NormalizationConfig()

    return ScoringConfig(
        state_multipliers=StateMultipliers(
            idle=state_mult_data.get("idle", m.idle),
            sleeping=state_mult_data.get("sleeping", m.sleeping),
            stopped=state_mult_data.get("stopped", m.stopped),
            zombie=state_mult_data.get("zombie", m.zombie),
            running=state_mult_data.get("running", m.running),
            stuck=state_mult_data.get("stuck", m.stuck),
        ),
        normalization=NormalizationConfig(
            cpu=norm_data.get("cpu", n.cpu),
            mem_gb=norm_data.get("mem_gb", n.mem_gb),
            pageins_rate=norm_data.get("pageins_rate", n.pageins_rate),
            faults_rate=norm_data.get("faults_rate", n.faults_rate),
            csw_rate=norm_data.get("csw_rate", n.csw_rate),
            syscalls_rate=norm_data.get("syscalls_rate", n.syscalls_rate),
            mach_msgs_rate=norm_data.get("mach_msgs_rate", n.mach_msgs_rate),
            wakeups_rate=norm_data.get("wakeups_rate", n.wakeups_rate),
            disk_io_rate=norm_data.get("disk_io_rate", n.disk_io_rate),
            runnable_time_rate=norm_data.get("runnable_time_rate", n.runnable_time_rate),
            qos_interactive_rate=norm_data.get("qos_interactive_rate", n.qos_interactive_rate),
            gpu_time_rate=norm_data.get("gpu_time_rate", n.gpu_time_rate),
            threads=norm_data.get("threads", n.threads),
            ipc_min=norm_data.get("ipc_min", n.ipc_min),
            zombie_children=norm_data.get("zombie_children", n.zombie_children),
        ),
    )


def _load_rogue_selection_config(data: dict) -> RogueSelectionConfig:
    """Load rogue selection config from TOML data."""
    d = RogueSelectionConfig()
    return RogueSelectionConfig(
        score_threshold=data.get("score_threshold", d.score_threshold),
        max_count=data.get("max_count", d.max_count),
    )


def _load_tui_config(data: dict) -> TUIConfig:
    """Load TUI config from TOML data.

    Handles nested [tui.colors.*] sections with defaults for missing values.
    """
    colors_data = data.get("colors", {})
    bands_data = colors_data.get("bands", {})
    trends_data = colors_data.get("trends", {})
    categories_data = colors_data.get("categories", {})
    status_data = colors_data.get("status", {})
    borders_data = colors_data.get("borders", {})

    # Use dataclass instances as single source of truth for defaults
    b = BandColors()
    t = TrendColors()
    c = CategoryColors()
    s = StatusColors()
    br = BorderColors()

    return TUIConfig(
        colors=TUIColorsConfig(
            bands=BandColors(
                low=bands_data.get("low", b.low),
                medium=bands_data.get("medium", b.medium),
                elevated=bands_data.get("elevated", b.elevated),
                high=bands_data.get("high", b.high),
                critical=bands_data.get("critical", b.critical),
            ),
            trends=TrendColors(
                worsening=trends_data.get("worsening", t.worsening),
                improving=trends_data.get("improving", t.improving),
                stable=trends_data.get("stable", t.stable),
                decayed=trends_data.get("decayed", t.decayed),
            ),
            categories=CategoryColors(
                blocking=categories_data.get("blocking", c.blocking),
                contention=categories_data.get("contention", c.contention),
                pressure=categories_data.get("pressure", c.pressure),
                efficiency=categories_data.get("efficiency", c.efficiency),
            ),
            status=StatusColors(
                active=status_data.get("active", s.active),
                ended=status_data.get("ended", s.ended),
            ),
            borders=BorderColors(
                normal=borders_data.get("normal", br.normal),
                elevated=borders_data.get("elevated", br.elevated),
                critical=borders_data.get("critical", br.critical),
                disconnected=borders_data.get("disconnected", br.disconnected),
            ),
        )
    )
