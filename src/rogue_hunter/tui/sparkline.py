"""Sparkline widget for visualizing numerical data over time.

A reusable, configurable sparkline that supports multi-row height,
color gradients, and multiple rendering modes.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import Enum
from typing import TYPE_CHECKING

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

if TYPE_CHECKING:
    from textual.app import RenderResult


def _parse_hex_color(hex_color: str) -> tuple[int, int, int]:
    """Parse a hex color string to RGB tuple.

    Args:
        hex_color: Color in format "#RRGGBB" or "#RGB".

    Returns:
        Tuple of (red, green, blue) integers 0-255.
    """
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        # Expand shorthand #RGB to #RRGGBB
        hex_color = "".join(c * 2 for c in hex_color)
    return (
        int(hex_color[0:2], 16),
        int(hex_color[2:4], 16),
        int(hex_color[4:6], 16),
    )


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convert RGB values to hex color string.

    Args:
        r: Red component 0-255.
        g: Green component 0-255.
        b: Blue component 0-255.

    Returns:
        Color in format "#RRGGBB".
    """
    return f"#{r:02x}{g:02x}{b:02x}"


def _lerp_color(
    color1: tuple[int, int, int],
    color2: tuple[int, int, int],
    t: float,
) -> tuple[int, int, int]:
    """Linearly interpolate between two RGB colors.

    Args:
        color1: Starting RGB color.
        color2: Ending RGB color.
        t: Interpolation factor 0.0-1.0 (0=color1, 1=color2).

    Returns:
        Interpolated RGB color.
    """
    t = max(0.0, min(1.0, t))
    return (
        int(color1[0] + (color2[0] - color1[0]) * t),
        int(color1[1] + (color2[1] - color1[1]) * t),
        int(color1[2] + (color2[2] - color1[2]) * t),
    )


class GradientColor:
    """A color gradient that interpolates between color stops.

    Create smooth color transitions based on value thresholds.

    Example:
        ```python
        gradient = GradientColor([
            (0, "#50fa7b"),    # Green at 0
            (50, "#f1fa8c"),   # Yellow at 50
            (100, "#ff5555"),  # Red at 100
        ])
        color = gradient(35)  # Returns interpolated green-yellow
        ```
    """

    def __init__(self, stops: list[tuple[float, str]]) -> None:
        """Initialize gradient with color stops.

        Args:
            stops: List of (threshold, hex_color) tuples, sorted by threshold.
                   Must have at least 2 stops.
        """
        if len(stops) < 2:
            raise ValueError("Gradient requires at least 2 color stops")
        # Sort by threshold
        self._stops = sorted(stops, key=lambda s: s[0])
        # Pre-parse colors for efficiency
        self._parsed: list[tuple[float, tuple[int, int, int]]] = [
            (threshold, _parse_hex_color(color)) for threshold, color in self._stops
        ]

    def __call__(self, value: float) -> str:
        """Get interpolated color for a value.

        Args:
            value: The value to get color for.

        Returns:
            Hex color string interpolated between stops.
        """
        # Handle edge cases
        if value <= self._parsed[0][0]:
            return _rgb_to_hex(*self._parsed[0][1])
        if value >= self._parsed[-1][0]:
            return _rgb_to_hex(*self._parsed[-1][1])

        # Find the two stops to interpolate between
        for i in range(len(self._parsed) - 1):
            t1, c1 = self._parsed[i]
            t2, c2 = self._parsed[i + 1]
            if t1 <= value <= t2:
                # Calculate interpolation factor
                t = (value - t1) / (t2 - t1) if t2 != t1 else 0.0
                rgb = _lerp_color(c1, c2, t)
                return _rgb_to_hex(*rgb)

        # If we reach here, the algorithm has a bug — value should always
        # fall between some consecutive pair after edge case handling
        raise AssertionError(f"Gradient interpolation failed for value {value}")


class SparklineOrientation(Enum):
    """Vertical growth direction for sparkline bars."""

    NORMAL = "normal"  # Bars grow upward from bottom
    INVERTED = "inverted"  # Bars grow downward from top
    MIRRORED = "mirrored"  # Bars grow outward from center (waveform style)


class SparklineDirection(Enum):
    """Horizontal flow direction for sparkline data."""

    RTL = "rtl"  # Right-to-left: newest on right, wave scrolls left (standard time series)
    LTR = "ltr"  # Left-to-right: newest on left, wave scrolls right


class Sparkline(Static):
    """A sparkline widget for visualizing numerical data over time.

    The sparkline displays data as vertical bars using Unicode characters.
    Supports multiple rows for increased vertical resolution.

    Values are scaled to fit the vertical range:
    - height=1: 8 levels (▁ to █)
    - height=2: 16 levels (bottom row fills first, then top)
    - height=3: 24 levels
    - height=4: 32 levels

    Example:
        ```python
        sparkline = Sparkline(height=2, max_value=100)
        sparkline.append(42)  # Add single value
        sparkline.data = [10, 20, 30]  # Replace all data
        ```
    """

    # Character sets (9 levels: empty + 8 filled)
    # Block elements: solid bars, clearest visual (NORMAL orientation only)
    CHARS_BLOCKS = " ▁▂▃▄▅▆▇█"
    # Braille normal: fills from bottom to top (fallback for inverted/mirrored)
    CHARS_BRAILLE_NORMAL = " ⡀⣀⣄⣤⣦⣶⣷⣿"
    # Braille inverted: fills from top to bottom (for inverted and mirrored bottom half)
    CHARS_BRAILLE_INVERTED = " ⠁⠉⠋⠛⠟⠿⡿⣿"
    LEVELS_PER_ROW = 8

    DEFAULT_CSS = """
    Sparkline {
        width: 1fr;
        height: auto;
    }
    """

    # Reactive property - triggers re-render on change
    data: reactive[list[float]] = reactive(list, always_update=True)

    def __init__(
        self,
        height: int = 1,
        max_value: float | None = 100,
        min_value: float = 0,
        orientation: SparklineOrientation = SparklineOrientation.NORMAL,
        direction: SparklineDirection = SparklineDirection.RTL,
        color_func: Callable[[float], str] | None = None,
        summary_func: Callable[[Sequence[float]], float] = max,
        **kwargs,
    ) -> None:
        """Initialize sparkline.

        Args:
            height: Number of character rows (1-4). Each row adds 8 levels.
            max_value: Maximum value for scaling. None for auto-scale.
            min_value: Minimum value for scaling.
            orientation: Vertical growth direction (NORMAL, INVERTED, or MIRRORED).
            direction: Horizontal flow direction (RTL or LTR).
            color_func: Function mapping value to Rich color string.
            summary_func: Function to summarize data chunks when width < data length.
            **kwargs: Passed to Static.__init__
        """
        super().__init__(**kwargs)
        self._height = max(1, min(4, height))  # Clamp to 1-4
        self._max_value = max_value
        self._min_value = min_value
        self._orientation = orientation
        self._direction = direction
        self._color_func = color_func
        self._summary_func = summary_func
        self._width = 0  # Updated on resize

    def on_resize(self) -> None:
        """Handle resize by updating width and trimming data."""
        self._width = self.size.width
        # Trim data to fit width
        if self._width > 0 and len(self.data) > self._width:
            self.data = self.data[-self._width :]

    def append(self, value: float) -> None:
        """Append a single value to the data buffer.

        If the data exceeds the widget width, older values are trimmed.
        """
        new_data = list(self.data)
        new_data.append(value)
        # Trim to width if we know it
        if self._width > 0 and len(new_data) > self._width:
            new_data = new_data[-self._width :]
        self.data = new_data

    def clear(self) -> None:
        """Clear all data."""
        self.data = []

    def render(self) -> RenderResult:
        """Render the sparkline as Rich Text."""
        if not self.data:
            return Text(" " * max(1, self._width))

        # Calculate effective max for scaling
        effective_max = self._max_value
        if effective_max is None:
            effective_max = max(self.data) if self.data else 1.0
        if effective_max <= self._min_value:
            effective_max = self._min_value + 1.0

        # Build rows (bottom to top for normal, top to bottom for inverted/mirrored)
        rows: list[Text] = [Text() for _ in range(self._height)]

        # Calculate padding for alignment during fill phase
        padding_count = max(0, self._width - len(self.data)) if self._width > 0 else 0

        # For RTL: right-align (pad left), iterate data in order
        # For LTR: left-align (pad right), iterate data in reverse
        if self._direction == SparklineDirection.RTL:
            # Add left padding first (right-align)
            if padding_count > 0:
                for row in rows:
                    row.append(" " * padding_count)
            data_iter = self.data
        else:
            # LTR: iterate in reverse (newest first on left)
            data_iter = reversed(self.data)

        for value in data_iter:
            level = self._scale_value(value, effective_max)
            column_chars = self._render_column(level)
            color = self._get_color(value)

            # Apply characters to rows
            for row_idx, char in enumerate(column_chars):
                if color:
                    rows[row_idx].append(char, style=color)
                else:
                    rows[row_idx].append(char)

        # For LTR: add right padding after data (left-align)
        if self._direction == SparklineDirection.LTR and padding_count > 0:
            for row in rows:
                row.append(" " * padding_count)

        # Combine rows into final output
        # For normal: rows are bottom-to-top, so reverse for display
        # For inverted/mirrored: rows are already top-to-bottom
        if self._orientation == SparklineOrientation.NORMAL:
            display_rows = list(reversed(rows))
        else:
            display_rows = rows

        # Join rows with newlines
        result = Text()
        for i, row in enumerate(display_rows):
            if i > 0:
                result.append("\n")
            result.append(row)

        return result

    def _scale_value(self, value: float, effective_max: float) -> int:
        """Scale a value to appropriate level range.

        For NORMAL/INVERTED: 0..(height * LEVELS_PER_ROW)
        For MIRRORED: 0..(half_height * LEVELS_PER_ROW) - each half renders this independently

        Args:
            value: The value to scale.
            effective_max: The maximum value for scaling.

        Returns:
            Integer level appropriate for the orientation.
        """
        if self._orientation == SparklineOrientation.MIRRORED:
            # Mirrored: scale to half-height levels, each half renders the same level
            half_height = self._height // 2
            if half_height == 0:
                half_height = 1  # height=1 edge case
            total_levels = half_height * self.LEVELS_PER_ROW
        else:
            total_levels = self._height * self.LEVELS_PER_ROW

        # Normalize to 0-1 range
        normalized = (value - self._min_value) / (effective_max - self._min_value)
        normalized = max(0.0, min(1.0, normalized))  # Clamp
        # Scale to levels
        level = int(normalized * total_levels)
        return max(0, min(total_levels, level))

    def _level_to_char(self, remaining: int, inverted_visual: bool = False) -> str:
        """Convert a remaining level count to the appropriate character.

        Uses solid block elements for NORMAL orientation (clearest visual),
        falls back to braille for INVERTED/MIRRORED (which need top-down fill).

        Args:
            remaining: Levels remaining for this row (can be negative, 0, partial, or full).
            inverted_visual: If True, use chars that fill from top-to-bottom visually.

        Returns:
            Character for the level.
        """
        # Choose character set based on orientation and visual direction
        if inverted_visual:
            # Inverted visual (top-down fill) - must use braille
            chars = self.CHARS_BRAILLE_INVERTED
        elif self._orientation == SparklineOrientation.NORMAL:
            # Normal orientation - use solid blocks for clearest visual
            chars = self.CHARS_BLOCKS
        else:
            # Mirrored top half or other cases - use braille
            chars = self.CHARS_BRAILLE_NORMAL

        if remaining <= 0:
            return chars[0]  # Empty
        elif remaining >= self.LEVELS_PER_ROW:
            return chars[self.LEVELS_PER_ROW]  # Full
        else:
            return chars[remaining]  # Partial

    def _render_column_mirrored(self, level: int) -> list[str]:
        """Render a mirrored column (waveform style - bars grow outward from center).

        - Top half: normal braille (visual fills bottom-to-top, rows fill bottom-to-top)
        - Center (odd heights): always solid if level > 0
        - Bottom half: inverted braille (visual fills top-to-bottom, rows fill top-to-bottom)

        Both halves render the same level independently - full granularity preserved.

        Args:
            level: The scaled level (0 to half_height * LEVELS_PER_ROW).

        Returns:
            List of characters in top-to-bottom order.
        """
        result: list[str] = [" "] * self._height
        half_height = self._height // 2
        has_center = self._height % 2 == 1

        if self._height == 1:
            # Edge case: height=1 is just a center row
            result[0] = self._level_to_char(self.LEVELS_PER_ROW if level > 0 else 0)
            return result

        # Top half: rows 0..(half_height-1), normal braille (grows upward visually)
        # Row half_height-1 is "bottom" of top half, fills first
        for row in range(half_height):
            row_from_bottom = half_height - 1 - row
            levels_below = row_from_bottom * self.LEVELS_PER_ROW
            remaining = level - levels_below
            result[row] = self._level_to_char(remaining, inverted_visual=False)

        # Center row (odd heights only): always solid if level > 0
        if has_center:
            center_idx = half_height
            result[center_idx] = self._level_to_char(self.LEVELS_PER_ROW if level > 0 else 0)

        # Bottom half: inverted braille (grows downward visually)
        # Row at bottom_start fills first (closest to center)
        bottom_start = half_height + (1 if has_center else 0)
        for i in range(half_height):
            row_idx = bottom_start + i
            levels_above = i * self.LEVELS_PER_ROW
            remaining = level - levels_above
            result[row_idx] = self._level_to_char(remaining, inverted_visual=True)

        return result

    def _render_column(self, level: int) -> list[str]:
        """Render a single column as list of characters.

        Args:
            level: The scaled level.

        Returns:
            List of characters (top-to-bottom for inverted/mirrored, bottom-to-top for normal).
        """
        if self._orientation == SparklineOrientation.MIRRORED:
            return self._render_column_mirrored(level)

        # For inverted mode, use inverted visual characters (fill from top of cell)
        inverted_visual = self._orientation == SparklineOrientation.INVERTED
        result: list[str] = []

        for row in range(self._height):
            if self._orientation == SparklineOrientation.INVERTED:
                # Inverted: fill from top down
                # Row 0 is top, fills first
                row_from_top = row
                levels_above = row_from_top * self.LEVELS_PER_ROW
                remaining = level - levels_above
            else:
                # Normal: fill from bottom up
                # Row 0 is bottom, fills first
                levels_below = row * self.LEVELS_PER_ROW
                remaining = level - levels_below

            result.append(self._level_to_char(remaining, inverted_visual=inverted_visual))

        return result

    def _get_color(self, value: float) -> str:
        """Get color for a value using color_func or default.

        Args:
            value: The original data value.

        Returns:
            Rich color string, or empty string for default.
        """
        if self._color_func is None:
            return ""
        return self._color_func(value)

    def watch_data(self, new_data: list[float]) -> None:
        """React to data changes by refreshing the widget."""
        self.refresh()
