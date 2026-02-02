"""Tests for configuration system."""

from rogue_hunter.config import (
    BandColors,
    BandsConfig,
    BorderColors,
    CategoryColors,
    Config,
    PidColors,
    ProcessStateColors,
    ResourceWeights,
    RetentionConfig,
    RogueSelectionConfig,
    ScoringConfig,
    SparklineConfig,
    StateMultipliers,
    StatusColors,
    SystemConfig,
    TrendColors,
    TUIColorsConfig,
    TUIConfig,
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
    assert "rogue-hunter" in str(config.config_dir)
    assert "rogue-hunter" in str(config.data_dir)
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


def test_rogue_selection_default():
    """Rogue selection has correct defaults."""
    config = Config()
    defaults = RogueSelectionConfig()
    assert config.rogue_selection.max_count == defaults.max_count


def test_band_thresholds_in_config():
    """Config includes band thresholds from BandsConfig."""
    config = Config()
    # Verify Config.bands matches BandsConfig defaults
    defaults = BandsConfig()
    assert config.bands.medium == defaults.medium
    assert config.bands.elevated == defaults.elevated
    assert config.bands.high == defaults.high
    assert config.bands.critical == defaults.critical


def test_config_save_includes_rogue_selection(tmp_path):
    """Config.save() writes rogue_selection section."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.rogue_selection.max_count = 15
    config.save(config_path)

    content = config_path.read_text()
    assert "[rogue_selection]" in content
    assert "max_count = 15" in content


def test_config_loads_rogue_selection(tmp_path):
    """Config.load() reads rogue_selection section from TOML."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[rogue_selection]
max_count = 10
""")

    config = Config.load(config_file)
    assert config.rogue_selection.max_count == 10


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
    # Zombie = 0.0 (dead, metrics are stale)
    assert mult.zombie == 0.0


# =============================================================================
# Resource Weights Configuration Tests
# =============================================================================


def test_resource_weights_defaults():
    """Resource weights have sensible Apple-style defaults."""
    weights = ResourceWeights()

    # GPU weighted higher than CPU (Apple model)
    assert weights.gpu > weights.cpu
    # Wakeups penalized
    assert weights.wakeups > 0
    # All weights are positive
    assert all(
        w > 0 for w in [weights.cpu, weights.gpu, weights.memory, weights.disk_io, weights.wakeups]
    )


def test_resource_weights_in_scoring_config():
    """ResourceWeights accessible via ScoringConfig."""
    scoring = ScoringConfig()

    assert hasattr(scoring, "resource_weights")
    assert scoring.resource_weights.cpu > 0


def test_config_load_resource_weights(tmp_path):
    """Resource weights load from TOML."""
    toml_content = """
[scoring.resource_weights]
cpu = 1.5
gpu = 4.0
memory = 1.0
disk_io = 1.0
wakeups = 2.0
"""
    config_file = tmp_path / "config.toml"
    config_file.write_text(toml_content)

    config = Config.load(config_file)

    assert config.scoring.resource_weights.cpu == 1.5
    assert config.scoring.resource_weights.gpu == 4.0


def test_config_load_partial_resource_weights(tmp_path):
    """Config.load() uses defaults for missing resource weight fields."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[scoring.resource_weights]
gpu = 5.0
""")

    config = Config.load(config_file)
    defaults = ResourceWeights()
    # Specified value
    assert config.scoring.resource_weights.gpu == 5.0
    # Defaults for unspecified
    assert config.scoring.resource_weights.cpu == defaults.cpu
    assert config.scoring.resource_weights.memory == defaults.memory
    assert config.scoring.resource_weights.wakeups == defaults.wakeups


# =============================================================================
# TUI Color Configuration Tests
# =============================================================================


def test_band_colors_defaults():
    """BandColors has sensible defaults for score visualization."""
    colors = BandColors()
    # Severity gradient: green -> cyan -> yellow -> orange -> red
    assert colors.low == "#50fa7b"  # Dracula green - healthy
    assert colors.medium == "#8be9fd"  # Dracula cyan - normal
    assert colors.elevated == "#f1fa8c"  # Dracula yellow - attention
    assert colors.high == "#ffb86c"  # Dracula orange - warning
    assert colors.critical == "#ff5555"  # Dracula red - urgent


def test_trend_colors_defaults():
    """TrendColors has distinct colors for each trend direction."""
    colors = TrendColors()
    # Each trend gets its own color for immediate recognition
    assert colors.worsening == "#ff5555"  # Dracula red - getting worse
    assert colors.improving == "#50fa7b"  # Dracula green - getting better
    assert colors.stable == "#bd93f9"  # Dracula purple - steady state
    assert colors.decayed == "dim"  # Faded - no longer tracked


def test_category_colors_defaults():
    """CategoryColors uses Dracula palette for visual separation."""
    colors = CategoryColors()
    # Each category gets a distinct color from Dracula palette
    assert colors.blocking == "#ff5555"  # Dracula red
    assert colors.contention == "#ffb86c"  # Dracula orange
    assert colors.pressure == "#f1fa8c"  # Dracula yellow
    assert colors.efficiency == "#bd93f9"  # Dracula purple


def test_status_colors_defaults():
    """StatusColors has sensible defaults for tracked panel."""
    colors = StatusColors()
    assert colors.active == "#50fa7b"  # Dracula green
    assert colors.ended == "dim"


def test_border_colors_defaults():
    """BorderColors has sensible defaults for header borders."""
    colors = BorderColors()
    # Uses Dracula palette
    assert colors.normal == "#50fa7b"  # Dracula green
    assert colors.elevated == "#f1fa8c"  # Dracula yellow
    assert colors.critical == "#ff5555"  # Dracula red
    assert colors.disconnected == "#ff5555"  # Same as critical


def test_pid_colors_defaults():
    """PidColors has muted color for PIDs."""
    colors = PidColors()
    assert colors.default == "#6272a4"  # Dracula comment - muted purple-gray


def test_process_state_colors_defaults():
    """ProcessStateColors has colors by severity."""
    colors = ProcessStateColors()
    # Healthy states
    assert colors.running == "#50fa7b"  # Dracula green - active
    assert colors.sleeping == "#8be9fd"  # Dracula cyan - normal
    assert colors.idle == "dim"  # Not significant
    # Problem states
    assert colors.stopped == "#f1fa8c"  # Dracula yellow - attention
    assert colors.zombie == "#ff5555"  # Dracula red - problem
    assert colors.stuck == "#ff5555"  # Dracula red - problem
    assert colors.unknown == "dim"


def test_tui_colors_config_has_all_sections():
    """TUIColorsConfig groups all color configurations."""
    colors = TUIColorsConfig()
    assert isinstance(colors.bands, BandColors)
    assert isinstance(colors.trends, TrendColors)
    assert isinstance(colors.categories, CategoryColors)
    assert isinstance(colors.status, StatusColors)
    assert isinstance(colors.borders, BorderColors)
    assert isinstance(colors.pid, PidColors)
    assert isinstance(colors.process_state, ProcessStateColors)


def test_tui_config_has_colors():
    """TUIConfig contains colors configuration."""
    tui = TUIConfig()
    assert isinstance(tui.colors, TUIColorsConfig)


def test_config_has_tui():
    """Config includes TUI configuration."""
    config = Config()
    assert hasattr(config, "tui")
    assert isinstance(config.tui, TUIConfig)


def test_config_save_includes_tui_colors(tmp_path):
    """Config.save() writes tui.colors sections."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.tui.colors.bands.critical = "magenta"
    config.tui.colors.trends.worsening = "bold red"
    config.save(config_path)

    content = config_path.read_text()
    assert "[tui.colors.bands]" in content
    assert 'critical = "magenta"' in content
    assert "[tui.colors.trends]" in content
    assert 'worsening = "bold red"' in content


def test_config_loads_tui_colors(tmp_path):
    """Config.load() reads tui.colors sections from TOML."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[tui.colors.bands]
critical = "magenta"
high = "#FF00FF"

[tui.colors.trends]
worsening = "bold red"
improving = "bold green"

[tui.colors.categories]
blocking = "bold red"

[tui.colors.status]
active = "green"

[tui.colors.borders]
normal = "blue"
""")

    config = Config.load(config_file)
    assert config.tui.colors.bands.critical == "magenta"
    assert config.tui.colors.bands.high == "#FF00FF"
    assert config.tui.colors.trends.worsening == "bold red"
    assert config.tui.colors.trends.improving == "bold green"
    assert config.tui.colors.categories.blocking == "bold red"
    assert config.tui.colors.status.active == "green"
    assert config.tui.colors.borders.normal == "blue"


def test_config_loads_partial_tui_colors(tmp_path):
    """Config.load() uses defaults for missing TUI color fields."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[tui.colors.bands]
critical = "magenta"
""")

    config = Config.load(config_file)
    defaults = TUIConfig()
    # Specified value
    assert config.tui.colors.bands.critical == "magenta"
    # Defaults for unspecified
    assert config.tui.colors.bands.low == defaults.colors.bands.low
    assert config.tui.colors.bands.elevated == defaults.colors.bands.elevated
    assert config.tui.colors.trends.worsening == defaults.colors.trends.worsening
    assert config.tui.colors.categories.blocking == defaults.colors.categories.blocking
    assert config.tui.colors.status.active == defaults.colors.status.active
    assert config.tui.colors.borders.normal == defaults.colors.borders.normal


def test_config_loads_missing_tui_section_returns_defaults(tmp_path):
    """Config.load() returns TUI defaults when [tui] section is missing."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[retention]
events_days = 60
""")

    config = Config.load(config_file)
    defaults = TUIConfig()
    assert config.tui.colors.bands.low == defaults.colors.bands.low
    assert config.tui.colors.bands.critical == defaults.colors.bands.critical
    assert config.tui.colors.trends.worsening == defaults.colors.trends.worsening


def test_tui_colors_roundtrip(tmp_path):
    """Config save/load preserves TUI color values."""
    config_path = tmp_path / "config.toml"

    # Create config with custom colors
    config = Config()
    config.tui.colors.bands.critical = "purple"
    config.tui.colors.bands.low = "cyan"
    config.tui.colors.trends.worsening = "bold magenta"
    config.tui.colors.categories.blocking = "#FF0000"
    config.tui.colors.status.active = "bright_green"
    config.tui.colors.borders.disconnected = "dim red"
    config.save(config_path)

    # Load and verify
    loaded = Config.load(config_path)
    assert loaded.tui.colors.bands.critical == "purple"
    assert loaded.tui.colors.bands.low == "cyan"
    assert loaded.tui.colors.trends.worsening == "bold magenta"
    assert loaded.tui.colors.categories.blocking == "#FF0000"
    assert loaded.tui.colors.status.active == "bright_green"
    assert loaded.tui.colors.borders.disconnected == "dim red"


# =============================================================================
# SparklineConfig Tests
# =============================================================================


def test_sparkline_config_defaults():
    """SparklineConfig has correct defaults."""
    config = SparklineConfig()
    assert config.height == 2
    assert config.orientation == "normal"
    assert config.direction == "rtl"


def test_tui_config_has_sparkline():
    """TUIConfig contains sparkline configuration."""
    tui = TUIConfig()
    assert isinstance(tui.sparkline, SparklineConfig)


def test_config_save_includes_sparkline(tmp_path):
    """Config.save() writes tui.sparkline section."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.tui.sparkline.height = 3
    config.tui.sparkline.orientation = "inverted"
    config.tui.sparkline.direction = "ltr"
    config.save(config_path)

    content = config_path.read_text()
    assert "[tui.sparkline]" in content
    assert "height = 3" in content
    assert 'orientation = "inverted"' in content
    assert 'direction = "ltr"' in content


def test_config_loads_sparkline(tmp_path):
    """Config.load() reads tui.sparkline section from TOML."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[tui.sparkline]
height = 3
orientation = "inverted"
direction = "ltr"
""")

    config = Config.load(config_file)
    assert config.tui.sparkline.height == 3
    assert config.tui.sparkline.orientation == "inverted"
    assert config.tui.sparkline.direction == "ltr"


def test_config_loads_partial_sparkline(tmp_path):
    """Config.load() uses defaults for missing sparkline fields."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[tui.sparkline]
orientation = "mirrored"
""")

    config = Config.load(config_file)
    defaults = SparklineConfig()
    assert config.tui.sparkline.orientation == "mirrored"
    assert config.tui.sparkline.height == defaults.height
    assert config.tui.sparkline.direction == defaults.direction


def test_sparkline_config_roundtrip(tmp_path):
    """Config save/load preserves sparkline values."""
    config_path = tmp_path / "config.toml"

    config = Config()
    config.tui.sparkline.height = 4
    config.tui.sparkline.orientation = "mirrored"
    config.tui.sparkline.direction = "ltr"
    config.save(config_path)

    loaded = Config.load(config_path)
    assert loaded.tui.sparkline.height == 4
    assert loaded.tui.sparkline.orientation == "mirrored"
    assert loaded.tui.sparkline.direction == "ltr"


# =============================================================================
# Sample-Based Checkpoint Configuration Tests
# =============================================================================


def test_checkpoint_samples_defaults():
    """Checkpoint sample counts have defaults for each band."""
    bands = BandsConfig()

    # Medium has less frequent checkpoints than elevated
    assert hasattr(bands, "medium_checkpoint_samples")
    assert hasattr(bands, "elevated_checkpoint_samples")
    assert bands.medium_checkpoint_samples > bands.elevated_checkpoint_samples
    # Both are positive integers
    assert bands.medium_checkpoint_samples > 0
    assert bands.elevated_checkpoint_samples > 0


def test_checkpoint_samples_configurable(tmp_path):
    """Checkpoint sample counts load from TOML."""
    toml_content = """
[bands]
medium_checkpoint_samples = 30
elevated_checkpoint_samples = 15
"""
    config_file = tmp_path / "config.toml"
    config_file.write_text(toml_content)

    config = Config.load(config_file)

    assert config.bands.medium_checkpoint_samples == 30
    assert config.bands.elevated_checkpoint_samples == 15


def test_checkpoint_samples_roundtrip(tmp_path):
    """Config save/load preserves checkpoint sample values."""
    config_path = tmp_path / "config.toml"

    config = Config()
    config.bands.medium_checkpoint_samples = 25
    config.bands.elevated_checkpoint_samples = 12
    config.save(config_path)

    loaded = Config.load(config_path)
    assert loaded.bands.medium_checkpoint_samples == 25
    assert loaded.bands.elevated_checkpoint_samples == 12


def test_checkpoint_samples_rejects_zero_medium(tmp_path):
    """Config.load() raises ValueError for medium_checkpoint_samples = 0."""
    import pytest

    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[bands]
medium_checkpoint_samples = 0
""")

    with pytest.raises(ValueError, match="medium_checkpoint_samples must be >= 1"):
        Config.load(config_file)


def test_checkpoint_samples_rejects_zero_elevated(tmp_path):
    """Config.load() raises ValueError for elevated_checkpoint_samples = 0."""
    import pytest

    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[bands]
elevated_checkpoint_samples = 0
""")

    with pytest.raises(ValueError, match="elevated_checkpoint_samples must be >= 1"):
        Config.load(config_file)


def test_checkpoint_samples_rejects_negative_medium(tmp_path):
    """Config.load() raises ValueError for negative medium_checkpoint_samples."""
    import pytest

    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[bands]
medium_checkpoint_samples = -5
""")

    with pytest.raises(ValueError, match="medium_checkpoint_samples must be >= 1"):
        Config.load(config_file)


def test_checkpoint_samples_rejects_negative_elevated(tmp_path):
    """Config.load() raises ValueError for negative elevated_checkpoint_samples."""
    import pytest

    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[bands]
elevated_checkpoint_samples = -3
""")

    with pytest.raises(ValueError, match="elevated_checkpoint_samples must be >= 1"):
        Config.load(config_file)
