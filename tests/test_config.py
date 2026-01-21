"""Tests for configuration system."""

from pause_monitor.config import (
    AlertsConfig,
    Config,
    RetentionConfig,
    SamplingConfig,
    SuspectsConfig,
)


def test_sampling_config_defaults():
    """SamplingConfig has correct defaults."""
    config = SamplingConfig()
    assert config.normal_interval == 5
    assert config.elevated_interval == 1
    assert config.elevation_threshold == 30
    assert config.critical_threshold == 60


def test_retention_config_defaults():
    """RetentionConfig has correct defaults."""
    config = RetentionConfig()
    assert config.samples_days == 30
    assert config.events_days == 90


def test_alerts_config_defaults():
    """AlertsConfig has correct defaults."""
    config = AlertsConfig()
    assert config.enabled is True
    assert config.pause_detected is True
    assert config.pause_min_duration == 2.0
    assert config.critical_stress is True
    assert config.critical_threshold == 60
    assert config.critical_duration == 30
    assert config.elevated_entered is False
    assert config.forensics_completed is True
    assert config.sound is True


def test_suspects_config_defaults():
    """SuspectsConfig has correct default patterns."""
    config = SuspectsConfig()
    assert "codemeter" in config.patterns
    assert "biomesyncd" in config.patterns
    assert "kernel_task" in config.patterns


def test_full_config_defaults():
    """Full Config object has correct nested defaults."""
    config = Config()
    assert config.sampling.normal_interval == 5
    assert config.retention.samples_days == 30
    assert config.alerts.enabled is True
    assert config.learning_mode is False


def test_config_paths():
    """Config provides correct data paths."""
    config = Config()
    assert "pause-monitor" in str(config.config_dir)
    assert "pause-monitor" in str(config.data_dir)
    assert config.db_path.name == "data.db"
    assert config.pid_path.name == "daemon.pid"


def test_config_save_creates_file(tmp_path):
    """Config.save() creates config file."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.save(config_path)
    assert config_path.exists()


def test_config_save_preserves_values(tmp_path):
    """Config.save() writes correct TOML values."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.sampling.normal_interval = 10
    config.learning_mode = True
    config.save(config_path)

    content = config_path.read_text()
    assert "normal_interval = 10" in content
    assert "learning_mode = true" in content


def test_config_load_reads_values(tmp_path):
    """Config.load() reads values from file."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("""
learning_mode = true

[sampling]
normal_interval = 10
elevation_threshold = 50

[alerts]
enabled = false
""")

    config = Config.load(config_path)
    assert config.learning_mode is True
    assert config.sampling.normal_interval == 10
    assert config.sampling.elevation_threshold == 50
    assert config.sampling.elevated_interval == 1  # Default preserved
    assert config.alerts.enabled is False


def test_config_load_missing_file_returns_defaults(tmp_path):
    """Config.load() returns defaults when file doesn't exist."""
    config_path = tmp_path / "nonexistent.toml"
    config = Config.load(config_path)
    assert config.sampling.normal_interval == 5
    assert config.learning_mode is False
