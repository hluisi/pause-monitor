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


@dataclass
class TiersConfig:
    """Tier threshold configuration."""

    elevated_threshold: int = 15
    critical_threshold: int = 50


@dataclass
class Config:
    """Main configuration container."""

    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    suspects: SuspectsConfig = field(default_factory=SuspectsConfig)
    sentinel: SentinelConfig = field(default_factory=SentinelConfig)
    tiers: TiersConfig = field(default_factory=TiersConfig)
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
        doc.add("sentinel", sentinel)
        doc.add(tomlkit.nl())

        tiers = tomlkit.table()
        tiers.add("elevated_threshold", self.tiers.elevated_threshold)
        tiers.add("critical_threshold", self.tiers.critical_threshold)
        doc.add("tiers", tiers)

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
            ),
            tiers=TiersConfig(
                elevated_threshold=tiers_data.get("elevated_threshold", 15),
                critical_threshold=tiers_data.get("critical_threshold", 50),
            ),
            learning_mode=data.get("learning_mode", False),
        )
