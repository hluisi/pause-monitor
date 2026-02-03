"""Tests for Sparkline widget."""

import pytest

from rogue_hunter.tui.sparkline import (
    GradientColor,
    Sparkline,
    SparklineDirection,
    SparklineOrientation,
)


class TestSparklineScaling:
    """Tests for value scaling to levels."""

    def test_scale_zero_to_zero_level(self) -> None:
        """Zero value scales to level 0."""
        sparkline = Sparkline(height=1, max_value=100, min_value=0)
        level = sparkline._scale_value(0, 100)
        assert level == 0

    def test_scale_max_to_max_level(self) -> None:
        """Max value scales to max level (height * 8)."""
        sparkline = Sparkline(height=1, max_value=100, min_value=0)
        level = sparkline._scale_value(100, 100)
        assert level == 8

    def test_scale_height_2_max_level(self) -> None:
        """Height=2 gives max level of 16."""
        sparkline = Sparkline(height=2, max_value=100, min_value=0)
        level = sparkline._scale_value(100, 100)
        assert level == 16

    def test_scale_mid_value(self) -> None:
        """50% value scales to half the levels."""
        sparkline = Sparkline(height=2, max_value=100, min_value=0)
        level = sparkline._scale_value(50, 100)
        assert level == 8

    def test_scale_clamps_below_min(self) -> None:
        """Values below min clamp to level 0."""
        sparkline = Sparkline(height=1, max_value=100, min_value=10)
        level = sparkline._scale_value(5, 100)
        assert level == 0

    def test_scale_clamps_above_max(self) -> None:
        """Values above max clamp to max level."""
        sparkline = Sparkline(height=1, max_value=100, min_value=0)
        level = sparkline._scale_value(150, 100)
        assert level == 8


class TestSparklineColumnRendering:
    """Tests for single column rendering with block characters (NORMAL mode)."""

    # Block chars for NORMAL: " ▁▂▃▄▅▆▇█"

    def test_render_column_empty(self) -> None:
        """Level 0 renders as space in all rows."""
        sparkline = Sparkline(height=2)
        chars = sparkline._render_column(0)
        assert chars == [" ", " "]

    def test_render_column_full_bottom_only(self) -> None:
        """Level 8 fills bottom row, top empty."""
        sparkline = Sparkline(height=2)
        chars = sparkline._render_column(8)
        # chars[0] is bottom row, chars[1] is top row
        assert chars[0] == "█"  # Bottom full
        assert chars[1] == " "  # Top empty

    def test_render_column_partial_bottom(self) -> None:
        """Partial level fills bottom row partially."""
        sparkline = Sparkline(height=2)
        chars = sparkline._render_column(4)
        assert chars[0] == "▄"  # Half filled block
        assert chars[1] == " "

    def test_render_column_overflow_to_top(self) -> None:
        """Level > 8 overflows to top row."""
        sparkline = Sparkline(height=2)
        chars = sparkline._render_column(12)
        assert chars[0] == "█"  # Bottom full
        assert chars[1] == "▄"  # Top partial (level 4)

    def test_render_column_full_both_rows(self) -> None:
        """Max level fills both rows."""
        sparkline = Sparkline(height=2)
        chars = sparkline._render_column(16)
        assert chars[0] == "█"
        assert chars[1] == "█"


class TestSparklineInvertedMode:
    """Tests for inverted rendering (bars grow downward visually)."""

    # Inverted Braille chars: " ⠁⠉⠋⠛⠟⠿⡿⣿"

    def test_inverted_partial_fills_from_top(self) -> None:
        """In inverted mode, partial values fill from top down."""
        sparkline = Sparkline(height=2, orientation=SparklineOrientation.INVERTED)
        chars = sparkline._render_column(4)
        # In inverted mode, row 0 is top, fills first, uses inverted visual chars
        assert chars[0] == "⠛"  # Top partial (inverted braille - fills from top of cell)
        assert chars[1] == " "  # Bottom empty

    def test_inverted_overflow_fills_second_row(self) -> None:
        """Inverted overflow fills bottom row."""
        sparkline = Sparkline(height=2, orientation=SparklineOrientation.INVERTED)
        chars = sparkline._render_column(12)
        assert chars[0] == "⣿"  # Top full
        assert chars[1] == "⠛"  # Bottom partial (inverted braille)


class TestSparklineCharacterSets:
    """Tests for character set selection (blocks for NORMAL, braille for others)."""

    def test_normal_uses_block_chars(self) -> None:
        """Normal mode uses solid block characters."""
        sparkline = Sparkline(height=1)
        # Level 4 should be half-filled block
        chars = sparkline._render_column(4)
        assert chars[0] == "▄"

    def test_normal_block_empty(self) -> None:
        """Normal mode uses space for empty."""
        sparkline = Sparkline(height=1)
        chars = sparkline._render_column(0)
        assert chars[0] == " "

    def test_normal_block_full(self) -> None:
        """Normal mode uses full block for max."""
        sparkline = Sparkline(height=1)
        chars = sparkline._render_column(8)
        assert chars[0] == "█"

    def test_inverted_uses_braille_chars(self) -> None:
        """Inverted mode uses top-to-bottom braille chars (no block equivalent)."""
        sparkline = Sparkline(height=1, orientation=SparklineOrientation.INVERTED)
        # Level 4 should be half-filled inverted braille (top to bottom)
        chars = sparkline._render_column(4)
        assert chars[0] == "⠛"

    def test_inverted_braille_full(self) -> None:
        """Inverted mode uses full braille for max."""
        sparkline = Sparkline(height=1, orientation=SparklineOrientation.INVERTED)
        chars = sparkline._render_column(8)
        assert chars[0] == "⣿"


class TestSparklineColorFunc:
    """Tests for color function callback."""

    def test_color_func_called_with_value(self) -> None:
        """Color function receives original value."""
        received_values: list[float] = []

        def capture_color(value: float) -> str:
            received_values.append(value)
            return "red"

        sparkline = Sparkline(height=1, color_func=capture_color)
        sparkline.data = [10, 20, 30]
        sparkline._width = 10  # Simulate resize
        # Trigger render to call color_func
        sparkline.render()

        assert received_values == [10, 20, 30]

    def test_no_color_func_returns_empty(self) -> None:
        """Without color_func, _get_color returns empty string."""
        sparkline = Sparkline(height=1)
        assert sparkline._get_color(50) == ""

    def test_color_func_return_value_used(self) -> None:
        """Color function return value is used."""
        sparkline = Sparkline(height=1, color_func=lambda v: "#ff0000")
        assert sparkline._get_color(50) == "#ff0000"


class TestSparklineDataManagement:
    """Tests for data append and clear."""

    def test_append_adds_value(self) -> None:
        """Append adds value to data."""
        sparkline = Sparkline(height=1)
        sparkline.append(42)
        assert sparkline.data == [42]

    def test_append_multiple_values(self) -> None:
        """Multiple appends accumulate."""
        sparkline = Sparkline(height=1)
        sparkline.append(10)
        sparkline.append(20)
        sparkline.append(30)
        assert sparkline.data == [10, 20, 30]

    def test_append_trims_to_width(self) -> None:
        """Append trims data to widget width."""
        sparkline = Sparkline(height=1)
        sparkline._width = 3  # Simulate resize
        sparkline.data = [1, 2, 3]
        sparkline.append(4)
        assert sparkline.data == [2, 3, 4]

    def test_clear_empties_data(self) -> None:
        """Clear removes all data."""
        sparkline = Sparkline(height=1)
        sparkline.data = [10, 20, 30]
        sparkline.clear()
        assert sparkline.data == []


class TestSparklineAutoScale:
    """Tests for auto-scaling when max_value=None."""

    def test_auto_scale_uses_max_data_value(self) -> None:
        """Auto-scale uses maximum value in data."""
        sparkline = Sparkline(height=1, max_value=None)
        sparkline.data = [10, 50, 30]
        # 50 is max, so 50 should scale to level 8
        level = sparkline._scale_value(50, 50)
        assert level == 8

    def test_auto_scale_half_of_max(self) -> None:
        """Half of auto-scaled max gives half levels."""
        sparkline = Sparkline(height=1, max_value=None)
        sparkline.data = [10, 50, 30]
        # 25 is half of 50
        level = sparkline._scale_value(25, 50)
        assert level == 4


class TestSparklineHeightClamping:
    """Tests for height parameter validation."""

    def test_height_clamps_to_minimum_1(self) -> None:
        """Height below 1 is clamped to 1."""
        sparkline = Sparkline(height=0)
        assert sparkline._height == 1

    def test_height_clamps_to_maximum_4(self) -> None:
        """Height above 4 is clamped to 4."""
        sparkline = Sparkline(height=10)
        assert sparkline._height == 4

    def test_height_accepts_valid_values(self) -> None:
        """Valid heights are accepted."""
        for h in [1, 2, 3, 4]:
            sparkline = Sparkline(height=h)
            assert sparkline._height == h


class TestSparklineRender:
    """Tests for full render output."""

    def test_render_empty_data(self) -> None:
        """Empty data renders as spaces."""
        sparkline = Sparkline(height=1)
        sparkline._width = 5
        result = sparkline.render()
        # Should return Text with spaces
        assert str(result) == "     "

    def test_render_single_value(self) -> None:
        """Single value renders correctly."""
        sparkline = Sparkline(height=1, max_value=100)
        sparkline._width = 10
        sparkline.data = [100]  # Max value = full block
        result = sparkline.render()
        assert "█" in str(result)

    def test_render_multirow_joins_with_newlines(self) -> None:
        """Multi-row render joins rows with newlines."""
        sparkline = Sparkline(height=2, max_value=100)
        sparkline._width = 10
        sparkline.data = [50]  # Half height = bottom row only
        result = sparkline.render()
        # Should have newline between rows
        assert "\n" in str(result)


class TestGradientColor:
    """Tests for gradient color interpolation."""

    def test_gradient_at_first_stop(self) -> None:
        """Value at first stop returns first color."""
        gradient = GradientColor([(0, "#000000"), (100, "#ffffff")])
        assert gradient(0) == "#000000"

    def test_gradient_at_last_stop(self) -> None:
        """Value at last stop returns last color."""
        gradient = GradientColor([(0, "#000000"), (100, "#ffffff")])
        assert gradient(100) == "#ffffff"

    def test_gradient_below_first_stop(self) -> None:
        """Value below first stop returns first color."""
        gradient = GradientColor([(10, "#000000"), (100, "#ffffff")])
        assert gradient(5) == "#000000"

    def test_gradient_above_last_stop(self) -> None:
        """Value above last stop returns last color."""
        gradient = GradientColor([(0, "#000000"), (50, "#ffffff")])
        assert gradient(100) == "#ffffff"

    def test_gradient_midpoint(self) -> None:
        """Value at midpoint returns interpolated color."""
        # Black to white, midpoint should be gray
        gradient = GradientColor([(0, "#000000"), (100, "#ffffff")])
        result = gradient(50)
        # Should be approximately #7f7f7f (gray)
        assert result.startswith("#")
        # Check it's in the middle range
        r = int(result[1:3], 16)
        g = int(result[3:5], 16)
        b = int(result[5:7], 16)
        assert 120 <= r <= 135  # Allow for rounding
        assert 120 <= g <= 135
        assert 120 <= b <= 135

    def test_gradient_multiple_stops(self) -> None:
        """Gradient with multiple stops interpolates correctly."""
        gradient = GradientColor(
            [
                (0, "#ff0000"),  # Red
                (50, "#00ff00"),  # Green
                (100, "#0000ff"),  # Blue
            ]
        )
        # At 0: pure red
        assert gradient(0) == "#ff0000"
        # At 50: pure green
        assert gradient(50) == "#00ff00"
        # At 100: pure blue
        assert gradient(100) == "#0000ff"
        # At 25: between red and green
        result = gradient(25)
        r = int(result[1:3], 16)
        g = int(result[3:5], 16)
        assert r > 100  # Still has significant red
        assert g > 100  # Has significant green

    def test_gradient_requires_two_stops(self) -> None:
        """Gradient raises error with fewer than 2 stops."""
        with pytest.raises(ValueError, match="at least 2"):
            GradientColor([(0, "#000000")])

    def test_gradient_sorts_stops(self) -> None:
        """Gradient sorts stops by threshold."""
        # Stops provided out of order
        gradient = GradientColor([(100, "#ffffff"), (0, "#000000")])
        assert gradient(0) == "#000000"
        assert gradient(100) == "#ffffff"

    def test_gradient_shorthand_hex(self) -> None:
        """Gradient handles shorthand hex colors (#RGB)."""
        gradient = GradientColor([(0, "#000"), (100, "#fff")])
        assert gradient(0) == "#000000"
        assert gradient(100) == "#ffffff"


class TestSparklineMirroredMode:
    """Tests for mirrored rendering (waveform style).

    Mirrored mode:
    - Top half uses normal braille (visual fills bottom-to-top)
    - Center row (odd heights) is always solid when level > 0
    - Bottom half uses inverted braille (visual fills top-to-bottom)

    Normal braille: " ⡀⣀⣄⣤⣦⣶⣷⣿" (fills from bottom of cell)
    Inverted braille: " ⠁⠉⠋⠛⠟⠿⡿⣿" (fills from top of cell)
    """

    def test_mirrored_height_4_empty(self) -> None:
        """Height=4 mirrored with level 0 is all empty."""
        sparkline = Sparkline(height=4, orientation=SparklineOrientation.MIRRORED)
        chars = sparkline._render_column(0)
        assert chars == [" ", " ", " ", " "]

    def test_mirrored_height_4_inner_fills_first(self) -> None:
        """Height=4 mirrored: inner rows (1,2) fill before outer rows (0,3).

        Level 4 (quarter) should partially fill inner rows only.
        Top half (row 1) uses normal braille, bottom half (row 2) uses inverted.
        """
        sparkline = Sparkline(height=4, orientation=SparklineOrientation.MIRRORED)
        chars = sparkline._render_column(4)
        assert chars[0] == " "  # Outer top empty
        assert chars[1] == "⣤"  # Inner top partial (normal braille)
        assert chars[2] == "⠛"  # Inner bottom partial (inverted braille)
        assert chars[3] == " "  # Outer bottom empty

    def test_mirrored_height_4_inner_full(self) -> None:
        """Height=4 mirrored: level 8 fills inner rows completely."""
        sparkline = Sparkline(height=4, orientation=SparklineOrientation.MIRRORED)
        chars = sparkline._render_column(8)
        assert chars[0] == " "  # Outer top still empty
        assert chars[1] == "⣿"  # Inner top full
        assert chars[2] == "⣿"  # Inner bottom full
        assert chars[3] == " "  # Outer bottom still empty

    def test_mirrored_height_4_overflow_to_outer(self) -> None:
        """Height=4 mirrored: level 12 overflows to outer rows."""
        sparkline = Sparkline(height=4, orientation=SparklineOrientation.MIRRORED)
        chars = sparkline._render_column(12)
        assert chars[0] == "⣤"  # Outer top partial (normal braille)
        assert chars[1] == "⣿"  # Inner top full
        assert chars[2] == "⣿"  # Inner bottom full
        assert chars[3] == "⠛"  # Outer bottom partial (inverted braille)

    def test_mirrored_height_4_full(self) -> None:
        """Height=4 mirrored: max level fills all rows."""
        sparkline = Sparkline(height=4, orientation=SparklineOrientation.MIRRORED)
        chars = sparkline._render_column(16)
        assert chars == ["⣿", "⣿", "⣿", "⣿"]

    def test_mirrored_height_3_center_always_solid(self) -> None:
        """Height=3 mirrored: center row (1) is always solid when level > 0."""
        sparkline = Sparkline(height=3, orientation=SparklineOrientation.MIRRORED)
        # Any non-zero level should have center solid
        # Top/bottom halves have 1 row each, scaling to 8 levels
        # Level 1 means partial fill on outer rows
        chars = sparkline._render_column(1)
        assert chars[0] == "⡀"  # Top outer partial (normal braille level 1)
        assert chars[1] == "⣿"  # Center always full
        assert chars[2] == "⠁"  # Bottom outer partial (inverted braille level 1)

    def test_mirrored_height_3_partial_outer(self) -> None:
        """Height=3 mirrored: partial values fill outer rows."""
        sparkline = Sparkline(height=3, orientation=SparklineOrientation.MIRRORED)
        chars = sparkline._render_column(4)
        assert chars[0] == "⣤"  # Top outer partial (normal braille)
        assert chars[1] == "⣿"  # Center full
        assert chars[2] == "⠛"  # Bottom outer partial (inverted braille)

    def test_mirrored_height_3_full(self) -> None:
        """Height=3 mirrored: max level fills all rows."""
        sparkline = Sparkline(height=3, orientation=SparklineOrientation.MIRRORED)
        chars = sparkline._render_column(8)
        assert chars == ["⣿", "⣿", "⣿"]

    def test_mirrored_height_2_symmetric(self) -> None:
        """Height=2 mirrored: top uses normal, bottom uses inverted braille."""
        sparkline = Sparkline(height=2, orientation=SparklineOrientation.MIRRORED)
        chars = sparkline._render_column(4)
        assert chars[0] == "⣤"  # Top partial (normal braille)
        assert chars[1] == "⠛"  # Bottom partial (inverted braille)

    def test_mirrored_height_2_full(self) -> None:
        """Height=2 mirrored: max level fills both rows."""
        sparkline = Sparkline(height=2, orientation=SparklineOrientation.MIRRORED)
        chars = sparkline._render_column(8)
        assert chars == ["⣿", "⣿"]

    def test_mirrored_height_1_binary(self) -> None:
        """Height=1 mirrored: acts like single center row (empty or solid)."""
        sparkline = Sparkline(height=1, orientation=SparklineOrientation.MIRRORED)
        chars_empty = sparkline._render_column(0)
        assert chars_empty == [" "]

        # Any non-zero should be solid (center always solid when level > 0)
        chars_solid = sparkline._render_column(1)
        assert chars_solid == ["⣿"]

    def test_mirrored_scaling_to_half_height(self) -> None:
        """Mirrored mode scales values to half-height levels."""
        sparkline = Sparkline(
            height=4,
            max_value=100,
            min_value=0,
            orientation=SparklineOrientation.MIRRORED,
        )
        # half_height=2, so max level should be 16
        level_max = sparkline._scale_value(100, 100)
        assert level_max == 16

        # 50% should give level 8
        level_half = sparkline._scale_value(50, 100)
        assert level_half == 8

        # 25% should give level 4
        level_quarter = sparkline._scale_value(25, 100)
        assert level_quarter == 4


class TestSparklineDirection:
    """Tests for horizontal flow direction (RTL vs LTR)."""

    def test_rtl_right_aligns_during_fill(self) -> None:
        """RTL mode right-aligns data during fill phase (pads left)."""
        sparkline = Sparkline(height=1, max_value=100, direction=SparklineDirection.RTL)
        sparkline._width = 5
        sparkline.data = [100, 100]  # 2 values, width 5
        result = sparkline.render()
        # Should be "   ██" (3 spaces, then 2 full blocks)
        assert str(result) == "   ██"

    def test_ltr_left_aligns_during_fill(self) -> None:
        """LTR mode left-aligns data during fill phase (pads right)."""
        sparkline = Sparkline(height=1, max_value=100, direction=SparklineDirection.LTR)
        sparkline._width = 5
        sparkline.data = [100, 100]  # 2 values, width 5
        result = sparkline.render()
        # Should be "██   " (2 full blocks, then 3 spaces)
        # Note: LTR reverses data order, so newest is on left
        assert str(result) == "██   "

    def test_rtl_data_order_oldest_left(self) -> None:
        """RTL mode: oldest data on left, newest on right."""
        sparkline = Sparkline(height=1, max_value=100, direction=SparklineDirection.RTL)
        sparkline._width = 3
        # Values: 25% → level 2 (▂), 50% → level 4 (▄), 100% → level 8 (█)
        sparkline.data = [25, 50, 100]
        result = sparkline.render()
        # Oldest (25) on left, newest (100) on right
        assert str(result) == "▂▄█"

    def test_ltr_data_order_newest_left(self) -> None:
        """LTR mode: newest data on left, oldest on right."""
        sparkline = Sparkline(height=1, max_value=100, direction=SparklineDirection.LTR)
        sparkline._width = 3
        # Values: 25% → level 2 (▂), 50% → level 4 (▄), 100% → level 8 (█)
        sparkline.data = [25, 50, 100]
        result = sparkline.render()
        # Newest (100) on left, oldest (25) on right (reversed)
        assert str(result) == "█▄▂"

    def test_default_direction_is_rtl(self) -> None:
        """Default direction is RTL."""
        sparkline = Sparkline(height=1)
        assert sparkline._direction == SparklineDirection.RTL

    def test_direction_works_with_all_orientations(self) -> None:
        """Direction works independently of orientation."""
        for orientation in SparklineOrientation:
            for direction in SparklineDirection:
                sparkline = Sparkline(
                    height=2,
                    max_value=100,
                    orientation=orientation,
                    direction=direction,
                )
                sparkline._width = 3
                sparkline.data = [50, 100]
                # Should not raise
                result = sparkline.render()
                assert result is not None
