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
    # Daemon heartbeat and logging
    heartbeat_samples: int = 60  # Log heartbeat every N samples (~20s at 3Hz)
    log_stability_samples: int = 3  # Samples before logging band transitions
    auto_prune_interval_hours: int = 24  # Hours between auto-prune runs
    # Log file rotation
    log_max_bytes: int = 5 * 1024 * 1024  # Max log file size (5MB)
    log_backup_count: int = 3  # Number of backup log files to keep
    # Forensics capture
    forensics_log_seconds: int = 60  # Seconds of logs to capture during forensics


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

    medium: int = 30  # Score to enter "medium" band (8x fair share)
    elevated: int = 45  # Score to enter "elevated" band (22x fair share)
    high: int = 60  # Score to enter "high" band (64x fair share)
    critical: int = 80  # Score to enter "critical" band (256x fair share)
    tracking_band: str = "medium"  # Tracking starts at medium band
    forensics_band: str = "critical"  # Only critical triggers forensics
    logging_band: str = "medium"  # Log entry at this band or higher, exit when dropping below
    # Sample-based checkpoint intervals
    medium_checkpoint_samples: int = 60  # ~20s at 3 samples/sec
    elevated_checkpoint_samples: int = 30  # ~10s at 3 samples/sec
    # Event debouncing to reduce noise from processes bouncing above/below threshold
    event_cooldown_seconds: float = 60.0  # Don't create new event for same PID within this time
    exit_stability_samples: int = 15  # Process must be below threshold for N samples before closing

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

    @property
    def logging_threshold(self) -> int:
        """Return the threshold for the logging band."""
        return self.get_threshold(self.logging_band)


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
class ResourceWeights:
    """Weights for each resource type in scoring (Apple-style model).

    Higher weight = more impact on score.
    GPU weighted higher because GPU work is intensive.
    """

    cpu: float = 1.0
    gpu: float = 2.0
    memory: float = 1.1
    disk_io: float = 1.2
    wakeups: float = 1.0


@dataclass
class ScoringConfig:
    """Scoring configuration.

    Contains resource weights, state multipliers, and fair share thresholds.
    """

    resource_weights: ResourceWeights = field(default_factory=ResourceWeights)
    state_multipliers: StateMultipliers = field(default_factory=StateMultipliers)
    # Thresholds for fair share resource user counting (affects scoring)
    share_min_cpu: float = 0.01  # CPU % threshold to count as resource user (0.01%)
    share_min_memory_bytes: int = 268_435_456  # Memory threshold (256 MiB)
    share_min_wakeups: float = 10.0  # Wakeups/sec threshold for resource user
    # Score curve tuning
    score_curve_multiplier: float = 10.0  # Multiplier for log2 scoring curve


@dataclass
class RogueSelectionConfig:
    """Configuration for rogue process selection.

    Top-N selection: selects highest-scoring processes up to max_count.
    Stuck processes are always included regardless of score.
    """

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
    - stable: No change - ●
    - decayed: Left rogues list - ○

    Default palette: Dracula theme.
    Each trend gets its own distinct color for immediate recognition.
    """

    worsening: str = "#ff5555"  # Dracula red - getting worse
    improving: str = "#50fa7b"  # Dracula green - getting better
    stable: str = "#bd93f9"  # Dracula purple - steady state
    decayed: str = "dim"  # Faded - no longer tracked


@dataclass
class PidColors:
    """Colors for PID column.

    PIDs should be visible but not compete with more important data.
    Default palette: Dracula theme.
    """

    default: str = "#6272a4"  # Dracula comment - muted purple-gray


@dataclass
class ProcessStateColors:
    """Colors for process state column (running, sleeping, zombie, etc.).

    Colors indicate state severity/concern level.
    Default palette: Dracula theme.
    """

    running: str = "#50fa7b"  # Dracula green - active, healthy
    sleeping: str = "#8be9fd"  # Dracula cyan - normal, waiting for I/O
    idle: str = "dim"  # Not significant
    stopped: str = "#f1fa8c"  # Dracula yellow - frozen (SIGSTOP/debugger)
    zombie: str = "#ff5555"  # Dracula red - dead, parent not reaping
    stuck: str = "#ff5555"  # Dracula red - uninterruptible, likely I/O issue
    unknown: str = "dim"


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
    pid: PidColors = field(default_factory=PidColors)
    process_state: ProcessStateColors = field(default_factory=ProcessStateColors)


@dataclass
class SparklineConfig:
    """Configuration for the sparkline widget in the header.

    The sparkline shows stress history as a mini chart using Braille characters.
    """

    height: int = 2  # Number of character rows (1-4). Each row adds 8 vertical levels.
    orientation: str = "normal"  # "normal" (up), "inverted" (down), "mirrored" (waveform)
    direction: str = "rtl"  # "rtl" (newest right, scrolls left), "ltr" (newest left, scrolls right)


@dataclass
class TUIConfig:
    """TUI-specific configuration."""

    colors: TUIColorsConfig = field(default_factory=TUIColorsConfig)
    sparkline: SparklineConfig = field(default_factory=SparklineConfig)
    # Display settings
    decay_seconds: float = 10.0  # Seconds to show dimmed processes after leaving rogues
    tracked_max_history: int = 15  # Max entries in tracked events panel
    activity_max_entries: int = 15  # Max entries in activity log
    command_truncate_length: int = 15  # Max chars for command in tracked panel
    # Reconnection settings
    reconnect_initial_delay: float = 1.0  # Initial reconnect delay (seconds)
    reconnect_max_delay: float = 30.0  # Max reconnect delay (seconds)
    reconnect_multiplier: float = 2.0  # Exponential backoff multiplier


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
                heartbeat_samples=system_data.get(
                    "heartbeat_samples", sys_defaults.heartbeat_samples
                ),
                log_stability_samples=system_data.get(
                    "log_stability_samples", sys_defaults.log_stability_samples
                ),
                auto_prune_interval_hours=system_data.get(
                    "auto_prune_interval_hours", sys_defaults.auto_prune_interval_hours
                ),
                log_max_bytes=system_data.get("log_max_bytes", sys_defaults.log_max_bytes),
                log_backup_count=system_data.get("log_backup_count", sys_defaults.log_backup_count),
                forensics_log_seconds=system_data.get(
                    "forensics_log_seconds", sys_defaults.forensics_log_seconds
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
    logging_band = data.get("logging_band", defaults.logging_band)

    if tracking_band not in valid_bands:
        raise ValueError(f"Invalid tracking_band: {tracking_band!r}. Must be one of {valid_bands}")
    if forensics_band not in valid_bands:
        raise ValueError(
            f"Invalid forensics_band: {forensics_band!r}. Must be one of {valid_bands}"
        )
    if logging_band not in valid_bands:
        raise ValueError(f"Invalid logging_band: {logging_band!r}. Must be one of {valid_bands}")

    medium_checkpoint_samples = data.get(
        "medium_checkpoint_samples", defaults.medium_checkpoint_samples
    )
    elevated_checkpoint_samples = data.get(
        "elevated_checkpoint_samples", defaults.elevated_checkpoint_samples
    )

    if medium_checkpoint_samples < 1:
        raise ValueError(f"medium_checkpoint_samples must be >= 1, got {medium_checkpoint_samples}")
    if elevated_checkpoint_samples < 1:
        raise ValueError(
            f"elevated_checkpoint_samples must be >= 1, got {elevated_checkpoint_samples}"
        )

    event_cooldown_seconds = data.get("event_cooldown_seconds", defaults.event_cooldown_seconds)
    exit_stability_samples = data.get("exit_stability_samples", defaults.exit_stability_samples)

    if event_cooldown_seconds < 0:
        raise ValueError(f"event_cooldown_seconds must be >= 0, got {event_cooldown_seconds}")
    if exit_stability_samples < 1:
        raise ValueError(f"exit_stability_samples must be >= 1, got {exit_stability_samples}")

    return BandsConfig(
        medium=data.get("medium", defaults.medium),
        elevated=data.get("elevated", defaults.elevated),
        high=data.get("high", defaults.high),
        critical=data.get("critical", defaults.critical),
        tracking_band=tracking_band,
        forensics_band=forensics_band,
        logging_band=logging_band,
        medium_checkpoint_samples=medium_checkpoint_samples,
        elevated_checkpoint_samples=elevated_checkpoint_samples,
        event_cooldown_seconds=event_cooldown_seconds,
        exit_stability_samples=exit_stability_samples,
    )


def _load_scoring_config(data: dict) -> ScoringConfig:
    """Load scoring config from TOML data."""
    weights_data = data.get("resource_weights", {})
    state_mult_data = data.get("state_multipliers", {})

    # Use dataclass instances as single source of truth for defaults
    defaults = ScoringConfig()
    w = defaults.resource_weights
    m = defaults.state_multipliers

    return ScoringConfig(
        resource_weights=ResourceWeights(
            cpu=weights_data.get("cpu", w.cpu),
            gpu=weights_data.get("gpu", w.gpu),
            memory=weights_data.get("memory", w.memory),
            disk_io=weights_data.get("disk_io", w.disk_io),
            wakeups=weights_data.get("wakeups", w.wakeups),
        ),
        state_multipliers=StateMultipliers(
            idle=state_mult_data.get("idle", m.idle),
            sleeping=state_mult_data.get("sleeping", m.sleeping),
            stopped=state_mult_data.get("stopped", m.stopped),
            zombie=state_mult_data.get("zombie", m.zombie),
            running=state_mult_data.get("running", m.running),
            stuck=state_mult_data.get("stuck", m.stuck),
        ),
        share_min_cpu=data.get("share_min_cpu", defaults.share_min_cpu),
        share_min_memory_bytes=data.get("share_min_memory_bytes", defaults.share_min_memory_bytes),
        share_min_wakeups=data.get("share_min_wakeups", defaults.share_min_wakeups),
        score_curve_multiplier=data.get("score_curve_multiplier", defaults.score_curve_multiplier),
    )


def _load_rogue_selection_config(data: dict) -> RogueSelectionConfig:
    """Load rogue selection config from TOML data."""
    d = RogueSelectionConfig()
    return RogueSelectionConfig(
        max_count=data.get("max_count", d.max_count),
    )


def _load_tui_config(data: dict) -> TUIConfig:
    """Load TUI config from TOML data.

    Handles nested [tui.colors.*] and [tui.sparkline] sections with defaults.
    """
    tui_defaults = TUIConfig()
    colors_data = data.get("colors", {})
    bands_data = colors_data.get("bands", {})
    trends_data = colors_data.get("trends", {})
    categories_data = colors_data.get("categories", {})
    status_data = colors_data.get("status", {})
    borders_data = colors_data.get("borders", {})
    pid_data = colors_data.get("pid", {})
    process_state_data = colors_data.get("process_state", {})
    sparkline_data = data.get("sparkline", {})

    # Use dataclass instances as single source of truth for defaults
    b = BandColors()
    t = TrendColors()
    c = CategoryColors()
    s = StatusColors()
    br = BorderColors()
    p = PidColors()
    ps = ProcessStateColors()
    sp = SparklineConfig()

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
            pid=PidColors(
                default=pid_data.get("default", p.default),
            ),
            process_state=ProcessStateColors(
                running=process_state_data.get("running", ps.running),
                sleeping=process_state_data.get("sleeping", ps.sleeping),
                idle=process_state_data.get("idle", ps.idle),
                stopped=process_state_data.get("stopped", ps.stopped),
                zombie=process_state_data.get("zombie", ps.zombie),
                stuck=process_state_data.get("stuck", ps.stuck),
                unknown=process_state_data.get("unknown", ps.unknown),
            ),
        ),
        sparkline=SparklineConfig(
            height=sparkline_data.get("height", sp.height),
            orientation=sparkline_data.get("orientation", sp.orientation),
            direction=sparkline_data.get("direction", sp.direction),
        ),
        # TUI display settings
        decay_seconds=data.get("decay_seconds", tui_defaults.decay_seconds),
        tracked_max_history=data.get("tracked_max_history", tui_defaults.tracked_max_history),
        activity_max_entries=data.get("activity_max_entries", tui_defaults.activity_max_entries),
        command_truncate_length=data.get(
            "command_truncate_length", tui_defaults.command_truncate_length
        ),
        reconnect_initial_delay=data.get(
            "reconnect_initial_delay", tui_defaults.reconnect_initial_delay
        ),
        reconnect_max_delay=data.get("reconnect_max_delay", tui_defaults.reconnect_max_delay),
        reconnect_multiplier=data.get("reconnect_multiplier", tui_defaults.reconnect_multiplier),
    )
