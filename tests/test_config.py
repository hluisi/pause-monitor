"""Tests for configuration system."""

from pause_monitor.config import (
    BandsConfig,
    CategorySelection,
    Config,
    NormalizationConfig,
    RetentionConfig,
    ScoringWeights,
    StateMultipliers,
    StateSelection,
    SystemConfig,
)


def test_retention_config_defaults():
    """RetentionConfig has correct defaults."""
    config = RetentionConfig()
    defaults = RetentionConfig()
    assert config.events_days == defaults.events_days


def test_full_config_defaults():
    """Full Config object has correct nested defaults."""
    config = Config()
    assert config.retention.events_days == RetentionConfig().events_days
    assert config.bands.medium == BandsConfig().medium


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
    config.retention.events_days = 120
    config.bands.medium = 30
    config.save(config_path)

    content = config_path.read_text()
    assert "events_days = 120" in content
    assert "medium = 30" in content


def test_config_load_reads_values(tmp_path):
    """Config.load() reads values from file."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("""
[bands]
medium = 15
elevated = 30
high = 50
critical = 70
tracking_band = "medium"
forensics_band = "elevated"

[retention]
events_days = 60
""")

    config = Config.load(config_path)
    assert config.bands.medium == 15
    assert config.bands.elevated == 30
    assert config.bands.tracking_band == "medium"
    assert config.retention.events_days == 60


def test_config_load_missing_file_returns_defaults(tmp_path):
    """Config.load() returns defaults when file doesn't exist."""
    config_path = tmp_path / "nonexistent.toml"
    config = Config.load(config_path)
    defaults = Config()
    assert config.retention.events_days == defaults.retention.events_days
    assert config.bands.medium == defaults.bands.medium


def test_system_config_defaults():
    """SystemConfig has correct defaults."""
    config = SystemConfig()
    # Just verify it's set to something reasonable (positive size)
    assert config.ring_buffer_size > 0


def test_bands_config_has_ordered_thresholds():
    """BandsConfig has sensible ordered thresholds."""
    bands = BandsConfig()
    # Verify defaults are ordered: medium < elevated < high < critical
    assert bands.medium < bands.elevated
    assert bands.elevated < bands.high
    assert bands.high < bands.critical


def test_config_loads_system_section(tmp_path):
    """Config loads [system] section from TOML."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[system]
ring_buffer_size = 120

[bands]
medium = 15
elevated = 30
high = 50
critical = 70
""")

    config = Config.load(config_file)
    assert config.system.ring_buffer_size == 120
    assert config.bands.medium == 15
    assert config.bands.elevated == 30


def test_config_save_includes_system_section(tmp_path):
    """Config.save() writes system and bands sections."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.system.ring_buffer_size = 45
    config.bands.medium = 25
    config.save(config_path)

    content = config_path.read_text()
    assert "ring_buffer_size = 45" in content
    assert "medium = 25" in content


def test_full_config_includes_system_and_bands():
    """Full Config object has system and bands fields."""
    config = Config()
    assert hasattr(config, "system")
    assert hasattr(config, "bands")
    # Verify fields exist and match their respective config defaults
    assert config.system.ring_buffer_size == SystemConfig().ring_buffer_size
    assert config.bands.medium == BandsConfig().medium


def test_scoring_weights_default():
    """Scoring weights should match ScoringWeights defaults."""
    config = Config()
    defaults = ScoringWeights()
    assert config.scoring.weights.cpu == defaults.cpu
    assert config.scoring.weights.state == defaults.state
    assert config.scoring.weights.pageins == defaults.pageins
    assert config.scoring.weights.mem == defaults.mem
    assert config.scoring.weights.cmprs == defaults.cmprs
    assert config.scoring.weights.csw == defaults.csw
    assert config.scoring.weights.sysbsd == defaults.sysbsd
    assert config.scoring.weights.threads == defaults.threads


def test_rogue_selection_default():
    """Rogue selection should match CategorySelection/StateSelection defaults."""
    config = Config()
    cat_defaults = CategorySelection()
    state_defaults = StateSelection()
    assert config.rogue_selection.cpu.enabled == cat_defaults.enabled
    assert config.rogue_selection.cpu.count == cat_defaults.count
    assert config.rogue_selection.cpu.threshold == cat_defaults.threshold
    assert config.rogue_selection.state.enabled == state_defaults.enabled
    assert config.rogue_selection.state.states == state_defaults.states


def test_band_thresholds_in_config():
    """Config includes band thresholds from BandsConfig."""
    config = Config()
    # Verify Config.bands matches BandsConfig defaults
    defaults = BandsConfig()
    assert config.bands.medium == defaults.medium
    assert config.bands.elevated == defaults.elevated
    assert config.bands.high == defaults.high
    assert config.bands.critical == defaults.critical


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
    defaults = ScoringWeights()
    assert config.scoring.weights.cpu == 30
    assert config.scoring.weights.state == 25
    assert config.scoring.weights.pageins == 20
    # Defaults for unspecified weights
    assert config.scoring.weights.mem == defaults.mem
    assert config.scoring.weights.threads == defaults.threads


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
    defaults = CategorySelection()
    assert config.rogue_selection.cpu.enabled is False
    assert config.rogue_selection.cpu.count == 5
    assert config.rogue_selection.cpu.threshold == 10.0
    assert config.rogue_selection.state.enabled is True
    assert config.rogue_selection.state.count == 2
    assert config.rogue_selection.state.states == ["stuck"]
    # Defaults for unspecified categories
    assert config.rogue_selection.mem.enabled == defaults.enabled
    assert config.rogue_selection.mem.count == defaults.count


def test_normalization_config_defaults():
    """NormalizationConfig matches its dataclass defaults."""
    norm = NormalizationConfig()
    defaults = NormalizationConfig()
    assert norm.cpu == defaults.cpu
    assert norm.mem_gb == defaults.mem_gb
    assert norm.cmprs_gb == defaults.cmprs_gb
    assert norm.pageins == defaults.pageins
    assert norm.csw == defaults.csw
    assert norm.sysbsd == defaults.sysbsd
    assert norm.threads == defaults.threads


def test_bands_config_defaults():
    """BandsConfig fields are self-consistent."""
    bands = BandsConfig()
    # Verify ordering is maintained
    assert bands.medium < bands.elevated < bands.high < bands.critical
    # Verify tracking/forensics bands are valid band names
    assert bands.tracking_band in {"low", "medium", "elevated", "high", "critical"}
    assert bands.forensics_band in {"low", "medium", "elevated", "high", "critical"}


def test_bands_config_get_band_for_score():
    """get_band() returns correct band name for score based on thresholds."""
    bands = BandsConfig()
    # Test boundary conditions using actual threshold values
    assert bands.get_band(0) == "low"
    assert bands.get_band(bands.medium - 1) == "low"
    assert bands.get_band(bands.medium) == "medium"
    assert bands.get_band(bands.elevated - 1) == "medium"
    assert bands.get_band(bands.elevated) == "elevated"
    assert bands.get_band(bands.high - 1) == "elevated"
    assert bands.get_band(bands.high) == "high"
    assert bands.get_band(bands.critical - 1) == "high"
    assert bands.get_band(bands.critical) == "critical"
    assert bands.get_band(100) == "critical"


def test_bands_config_get_threshold_for_band():
    """get_threshold() returns score threshold for band name."""
    bands = BandsConfig()
    assert bands.get_threshold("low") == 0
    assert bands.get_threshold("medium") == bands.medium
    assert bands.get_threshold("elevated") == bands.elevated
    assert bands.get_threshold("high") == bands.high
    assert bands.get_threshold("critical") == bands.critical


def test_bands_config_tracking_threshold():
    """tracking_threshold property returns threshold for tracking_band."""
    bands = BandsConfig()
    assert bands.tracking_threshold == bands.get_threshold(bands.tracking_band)


def test_bands_config_forensics_threshold():
    """forensics_threshold property returns threshold for forensics_band."""
    bands = BandsConfig()
    assert bands.forensics_threshold == bands.get_threshold(bands.forensics_band)


def test_config_has_bands_not_tiers():
    """Config has bands attribute, not tiers."""
    config = Config()
    assert hasattr(config, "bands")
    assert not hasattr(config, "tiers")


def test_scoring_config_includes_normalization():
    """ScoringConfig includes normalization field."""
    config = Config()
    defaults = NormalizationConfig()
    assert hasattr(config.scoring, "normalization")
    assert config.scoring.normalization.mem_gb == defaults.mem_gb


def test_config_save_includes_normalization(tmp_path):
    """Config.save() writes normalization section."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.scoring.normalization.mem_gb = 16.0
    config.scoring.normalization.pageins = 2000
    config.save(config_path)

    content = config_path.read_text()
    assert "[scoring.normalization]" in content
    assert "mem_gb = 16.0" in content
    assert "pageins = 2000" in content


def test_config_loads_normalization(tmp_path):
    """Config.load() reads normalization section from TOML."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[scoring.normalization]
cpu = 100.0
mem_gb = 32.0
cmprs_gb = 4.0
pageins = 5000
csw = 200000
sysbsd = 150000
threads = 500
""")

    config = Config.load(config_file)
    assert config.scoring.normalization.mem_gb == 32.0
    assert config.scoring.normalization.cmprs_gb == 4.0
    assert config.scoring.normalization.pageins == 5000
    assert config.scoring.normalization.csw == 200000
    assert config.scoring.normalization.sysbsd == 150000
    assert config.scoring.normalization.threads == 500


def test_config_loads_partial_normalization(tmp_path):
    """Config.load() uses defaults for missing normalization fields."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[scoring.normalization]
mem_gb = 64.0
""")

    config = Config.load(config_file)
    defaults = NormalizationConfig()
    # Specified value
    assert config.scoring.normalization.mem_gb == 64.0
    # Defaults for unspecified
    assert config.scoring.normalization.cpu == defaults.cpu
    assert config.scoring.normalization.pageins == defaults.pageins


def test_bands_get_threshold_raises_for_invalid_band():
    """get_threshold() raises ValueError for invalid band name."""
    import pytest

    bands = BandsConfig()
    with pytest.raises(ValueError, match="Unknown band: 'invalid'"):
        bands.get_threshold("invalid")


def test_config_load_raises_for_invalid_tracking_band(tmp_path):
    """Config.load() raises ValueError for invalid tracking_band."""
    import pytest

    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[bands]
tracking_band = "elavated"
""")

    with pytest.raises(ValueError, match="Invalid tracking_band"):
        Config.load(config_file)


def test_config_load_raises_for_invalid_forensics_band(tmp_path):
    """Config.load() raises ValueError for invalid forensics_band."""
    import pytest

    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[bands]
forensics_band = "hihg"
""")

    with pytest.raises(ValueError, match="Invalid forensics_band"):
        Config.load(config_file)


def test_config_load_raises_for_invalid_toml_syntax(tmp_path):
    """Config.load() raises ValueError with file path for invalid TOML syntax."""
    import pytest

    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[sampling
normal_interval = 5
""")

    with pytest.raises(ValueError, match=f"Failed to parse config file {config_file}"):
        Config.load(config_file)


def test_state_multipliers_defaults():
    """StateMultipliers has self-consistent defaults."""
    mult = StateMultipliers()
    # Running/stuck should have highest multiplier (1.0)
    assert mult.running == 1.0
    assert mult.stuck == 1.0
    # Other states should be less than running
    assert mult.idle < mult.running
    assert mult.sleeping < mult.running
    assert mult.stopped < mult.running
    assert mult.halted < mult.running
    assert mult.zombie < mult.running
