"""Configuration system for pause-monitor."""

from dataclasses import dataclass, field
from pathlib import Path


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
class Config:
    """Main configuration container."""

    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    suspects: SuspectsConfig = field(default_factory=SuspectsConfig)
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
