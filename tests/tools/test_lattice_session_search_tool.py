"""Tests for lattice_session_search (tools/lattice_tool.py)."""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest


class TestLatticeSessionSearchCheckFn:
    def test_available_on_all_platforms_when_db_exists(self, monkeypatch):
        for platform in ("telegram", "signal", "lattice"):
            monkeypatch.setenv("HERMES_SESSION_PLATFORM", platform)
            from tools.lattice_tool import check_lattice_session_search_requirements
            mock_path = MagicMock()
            mock_path.parent.exists.return_value = True
            mock_module = MagicMock()
            mock_module.DEFAULT_DB_PATH = mock_path
            with patch.dict("sys.modules", {"hermes_state": mock_module}):
                assert check_lattice_session_search_requirements() is True

    def test_available_on_telegram_when_db_exists(self, monkeypatch):
        monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
        from tools.lattice_tool import check_lattice_session_search_requirements
        mock_path = MagicMock()
        mock_path.parent.exists.return_value = True
        mock_module = MagicMock()
        mock_module.DEFAULT_DB_PATH = mock_path
        with patch.dict("sys.modules", {"hermes_state": mock_module}):
            assert check_lattice_session_search_requirements() is True


class TestLatticeSessionSearch:
    def test_returns_error_without_db(self):
        from tools.lattice_tool import lattice_session_search
        result = json.loads(lattice_session_search(query="test", db=None))
        assert result["success"] is False
        assert "database" in result["error"].lower()

    def test_scope_locked_to_lattice_source(self):
        """The underlying search_messages must always receive source_filter=["lattice"]."""
        from tools.lattice_tool import lattice_session_search

        mock_db = MagicMock()
        mock_db.search_messages.return_value = []

        lattice_session_search(query="deploy", db=mock_db)

        # search_messages must have been called with source_filter=["lattice"]
        calls = mock_db.search_messages.call_args_list
        assert len(calls) >= 1
        _, kwargs = calls[0]
        assert kwargs.get("source_filter") == ["lattice"] or calls[0][0][1] == ["lattice"]

    def test_recent_mode_scoped_to_lattice_source(self):
        """No-query (recent) mode must call list_sessions_rich with source='lattice'."""
        from tools.lattice_tool import lattice_session_search

        mock_db = MagicMock()
        mock_db.list_sessions_rich.return_value = []

        lattice_session_search(query="", db=mock_db)

        mock_db.list_sessions_rich.assert_called_once()
        _, kwargs = mock_db.list_sessions_rich.call_args
        assert kwargs.get("source") == "lattice"

    def test_schema_name(self):
        from tools.lattice_tool import LATTICE_SESSION_SEARCH_SCHEMA
        assert LATTICE_SESSION_SEARCH_SCHEMA["name"] == "lattice_session_search"
