# tests/test_tui_reconnect.py
"""Tests for TUI auto-reconnect behavior."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from textual.css.query import NoMatches


class TestTUIReconnect:
    """Tests for RogueHunterApp auto-reconnect."""

    @pytest.fixture
    def short_tmp_path(self):
        """Create a short temporary path for Unix sockets."""
        with tempfile.TemporaryDirectory(dir="/tmp", prefix="pm_") as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def mock_config(self, short_tmp_path):
        """Create a mock config with short socket path."""
        config = MagicMock()
        config.socket_path = short_tmp_path / "test.sock"
        return config

    def test_reconnect_backoff_config_defaults(self):
        """Verify reconnect backoff config defaults are correctly set."""
        from rogue_hunter.config import TUIConfig

        tui_config = TUIConfig()
        assert tui_config.reconnect_initial_delay == 1.0
        assert tui_config.reconnect_max_delay == 30.0
        assert tui_config.reconnect_multiplier == 2.0

    def test_backoff_calculation(self, mock_config):
        """Verify the backoff calculation logic."""
        from rogue_hunter.config import TUIConfig

        # Test the backoff calculation using config defaults
        tui_config = TUIConfig()
        initial = tui_config.reconnect_initial_delay
        multiplier = tui_config.reconnect_multiplier
        max_delay = tui_config.reconnect_max_delay

        # Calculate expected sequence
        delay = initial
        delays = [delay]
        for _ in range(10):
            delay = min(delay * multiplier, max_delay)
            delays.append(delay)

        # Verify exponential backoff: 1, 2, 4, 8, 16, 30 (capped)
        assert delays[0] == 1.0
        assert delays[1] == 2.0
        assert delays[2] == 4.0
        assert delays[3] == 8.0
        assert delays[4] == 16.0
        assert delays[5] == 30.0  # Capped at max
        assert delays[6] == 30.0  # Stays at max

    def test_max_delay_cap(self, mock_config):
        """Verify delay is capped at MAX_DELAY."""
        from rogue_hunter.config import TUIConfig

        tui_config = TUIConfig()
        max_delay = tui_config.reconnect_max_delay
        multiplier = tui_config.reconnect_multiplier

        # Even with a very large delay, it should be capped
        delay = 100.0
        capped = min(delay * multiplier, max_delay)
        assert capped == max_delay

    def test_stopping_flag_prevents_reconnect(self, mock_config):
        """Verify _stopping flag is checked in reconnect logic."""
        from rogue_hunter.tui.app import RogueHunterApp

        app = RogueHunterApp(config=mock_config)

        # When stopping, reconnect should not be started
        app._stopping = True
        app._reconnect_task = None
        app.query_one = MagicMock(side_effect=NoMatches("no widgets"))

        # This should not start a reconnect task
        with patch("asyncio.create_task") as mock_create:
            app._set_disconnected("error", start_reconnect=True)
            mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_disconnected_starts_reconnect(self, mock_config):
        """_set_disconnected should start reconnect loop when appropriate."""
        from rogue_hunter.tui.app import RogueHunterApp

        app = RogueHunterApp(config=mock_config)
        app._stopping = False
        app._reconnect_task = None

        # Mock query_one to avoid Textual widget issues
        app.query_one = MagicMock(side_effect=NoMatches("no widgets"))

        # Create a task that we'll check for
        mock_task = MagicMock()
        mock_task.done.return_value = False

        with patch("asyncio.create_task", return_value=mock_task) as mock_create:
            app._set_disconnected("test error", start_reconnect=True)

            # Should have started reconnect task
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_disconnected_no_reconnect_when_stopping(self, mock_config):
        """_set_disconnected should not start reconnect when stopping."""
        from rogue_hunter.tui.app import RogueHunterApp

        app = RogueHunterApp(config=mock_config)
        app._stopping = True  # Shutting down
        app._reconnect_task = None

        app.query_one = MagicMock(side_effect=NoMatches("no widgets"))

        with patch("asyncio.create_task") as mock_create:
            app._set_disconnected("test error", start_reconnect=True)

            # Should NOT have started reconnect task
            mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_try_socket_connect_returns_bool(self, mock_config, short_tmp_path):
        """_try_socket_connect should return True on success, False on failure."""
        from rogue_hunter.tui.app import RogueHunterApp

        app = RogueHunterApp(config=mock_config)
        app.query_one = MagicMock(side_effect=NoMatches("no widgets"))
        app.notify = MagicMock()

        # Test failure (socket doesn't exist)
        result = await app._try_socket_connect(show_notification=False)
        assert result is False

    @pytest.mark.asyncio
    async def test_initial_connect_starts_reconnect_on_failure(self, mock_config):
        """_initial_connect should start reconnect loop if connection fails."""
        from rogue_hunter.tui.app import RogueHunterApp

        app = RogueHunterApp(config=mock_config)
        app.query_one = MagicMock(side_effect=NoMatches("no widgets"))
        app.notify = MagicMock()
        app._reconnect_task = None

        mock_task = MagicMock()

        with patch("asyncio.create_task", return_value=mock_task) as mock_create:
            await app._initial_connect()

            # Should have started reconnect task since connection failed
            assert mock_create.called
