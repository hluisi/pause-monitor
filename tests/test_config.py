"""Tests for configuration system."""

from pause_monitor.config import (
    AlertsConfig,
    Config,
    RetentionConfig,
    SamplingConfig,
    SentinelConfig,
    SuspectsConfig,
    TiersConfig,
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


def test_sentinel_config_defaults():
    """SentinelConfig has correct defaults."""
    config = SentinelConfig()
    assert config.fast_interval_ms == 100
    assert config.ring_buffer_seconds == 30


def test_tiers_config_defaults():
    """TiersConfig has sensible defaults."""
    config = TiersConfig()
    # Verify defaults exist and are sensible (elevated < critical)
    assert config.elevated_threshold > 0
    assert config.critical_threshold > config.elevated_threshold


def test_config_loads_sentinel_section(tmp_path):
    """Config loads [sentinel] section from TOML."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[sentinel]
fast_interval_ms = 200
ring_buffer_seconds = 60

[tiers]
elevated_threshold = 20
critical_threshold = 60
""")

    config = Config.load(config_file)
    assert config.sentinel.fast_interval_ms == 200
    assert config.sentinel.ring_buffer_seconds == 60
    assert config.tiers.elevated_threshold == 20
    assert config.tiers.critical_threshold == 60


def test_config_save_includes_sentinel_section(tmp_path):
    """Config.save() writes sentinel and tiers sections."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.sentinel.fast_interval_ms = 150
    config.tiers.elevated_threshold = 25
    config.save(config_path)

    content = config_path.read_text()
    assert "fast_interval_ms = 150" in content
    assert "elevated_threshold = 25" in content


def test_full_config_includes_sentinel_and_tiers():
    """Full Config object has sentinel and tiers fields."""
    config = Config()
    assert hasattr(config, "sentinel")
    assert hasattr(config, "tiers")
    # Verify fields exist and match their respective config defaults
    assert config.sentinel.fast_interval_ms == SentinelConfig().fast_interval_ms
    assert config.tiers.elevated_threshold == TiersConfig().elevated_threshold


def test_config_loads_partial_sentinel_section(tmp_path):
    """Config loads [sentinel] with partial fields, using defaults for missing."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[sentinel]
fast_interval_ms = 200
# ring_buffer_seconds omitted
""")
    config = Config.load(config_file)

    # Specified value
    assert config.sentinel.fast_interval_ms == 200
    # Default value for omitted field
    assert config.sentinel.ring_buffer_seconds == 30


def test_sentinel_config_has_pause_threshold():
    """SentinelConfig should have pause detection threshold."""
    config = SentinelConfig()
    assert config.pause_threshold_ratio == 2.0  # Default: 2x expected latency


def test_sentinel_config_has_peak_tracking_interval():
    """SentinelConfig should have peak tracking interval."""
    config = SentinelConfig()
    assert config.peak_tracking_seconds == 30  # Default: one buffer cycle


def test_config_loads_new_sentinel_fields(tmp_path):
    """Config loads pause_threshold_ratio and peak_tracking_seconds."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[sentinel]
fast_interval_ms = 100
ring_buffer_seconds = 30
pause_threshold_ratio = 3.0
peak_tracking_seconds = 60
""")

    config = Config.load(config_file)
    assert config.sentinel.pause_threshold_ratio == 3.0
    assert config.sentinel.peak_tracking_seconds == 60


def test_config_save_includes_new_sentinel_fields(tmp_path):
    """Config.save() writes pause_threshold_ratio and peak_tracking_seconds."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.sentinel.pause_threshold_ratio = 2.5
    config.sentinel.peak_tracking_seconds = 45
    config.save(config_path)

    content = config_path.read_text()
    assert "pause_threshold_ratio = 2.5" in content
    assert "peak_tracking_seconds = 45" in content


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
    assert config.rogue_selection.state.states == ["zombie"]


def test_tier_thresholds_in_config():
    """Config includes tier thresholds from TiersConfig."""
    config = Config()
    # Verify Config.tiers matches TiersConfig defaults
    defaults = TiersConfig()
    assert config.tiers.elevated_threshold == defaults.elevated_threshold
    assert config.tiers.critical_threshold == defaults.critical_threshold


def test_config_save_includes_scoring_section(tmp_path):
    """Config.save() writes scoring section with weights."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.scoring.weights.cpu = 30
    config.scoring.weights.state = 25
    config.save(config_path)

    content = config_path.read_text()
    assert "[scoring.weights]" in content
    assert "cpu = 30" in content
    assert "state = 25" in content


def test_config_loads_scoring_section(tmp_path):
    """Config.load() reads scoring section from TOML."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[scoring.weights]
cpu = 30
state = 25
pageins = 20
""")

    config = Config.load(config_file)
    assert config.scoring.weights.cpu == 30
    assert config.scoring.weights.state == 25
    assert config.scoring.weights.pageins == 20
    # Defaults for unspecified weights
    assert config.scoring.weights.mem == 15
    assert config.scoring.weights.threads == 0


def test_config_save_includes_rogue_selection(tmp_path):
    """Config.save() writes rogue_selection section."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.rogue_selection.cpu.count = 5
    config.rogue_selection.state.states = ["stuck", "zombie", "uninterruptible"]
    config.save(config_path)

    content = config_path.read_text()
    assert "[rogue_selection.cpu]" in content
    assert "count = 5" in content
    assert "[rogue_selection.state]" in content


def test_config_loads_rogue_selection(tmp_path):
    """Config.load() reads rogue_selection section from TOML."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[rogue_selection.cpu]
enabled = false
count = 5
threshold = 10.0

[rogue_selection.state]
enabled = true
count = 2
states = ["stuck"]
""")

    config = Config.load(config_file)
    assert config.rogue_selection.cpu.enabled is False
    assert config.rogue_selection.cpu.count == 5
    assert config.rogue_selection.cpu.threshold == 10.0
    assert config.rogue_selection.state.enabled is True
    assert config.rogue_selection.state.count == 2
    assert config.rogue_selection.state.states == ["stuck"]
    # Defaults for unspecified categories
    assert config.rogue_selection.mem.enabled is True
    assert config.rogue_selection.mem.count == 3
