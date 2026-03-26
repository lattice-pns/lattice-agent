"""Tests for tools/lattice_session_search_tool.py"""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest


class TestLatticeSessionSearchCheckFn:
    def test_unavailable_on_lattice_platform(self, monkeypatch):
        monkeypatch.setenv("HERMES_SESSION_PLATFORM", "lattice")
        from tools.lattice_session_search_tool import check_lattice_session_search_requirements
        assert check_lattice_session_search_requirements() is False

    def test_available_on_telegram_when_db_exists(self, monkeypatch):
        monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
        from tools.lattice_session_search_tool import check_lattice_session_search_requirements
        mock_path = MagicMock()
        mock_path.parent.exists.return_value = True
        mock_module = MagicMock()
        mock_module.DEFAULT_DB_PATH = mock_path
        with patch.dict("sys.modules", {"hermes_state": mock_module}):
            assert check_lattice_session_search_requirements() is True


class TestLatticeSessionSearch:
    def test_returns_error_without_db(self):
        from tools.lattice_session_search_tool import lattice_session_search
        result = json.loads(lattice_session_search(query="test", db=None))
        assert result["success"] is False
        assert "database" in result["error"].lower()

    def test_scope_locked_to_lattice_source(self):
        """The underlying search_messages must always receive source_filter=["lattice"]."""
        from tools.lattice_session_search_tool import lattice_session_search

        mock_db = MagicMock()
        mock_db.search_messages.return_value = []

        lattice_session_search(query="deploy", db=mock_db)

        # search_messages must have been called with source_filter=["lattice"]
        calls = mock_db.search_messages.call_args_list
        assert len(calls) >= 1
        _, kwargs = calls[0]
        assert kwargs.get("source_filter") == ["lattice"] or calls[0][0][1] == ["lattice"]

    def test_schema_name(self):
        from tools.lattice_session_search_tool import LATTICE_SESSION_SEARCH_SCHEMA
        assert LATTICE_SESSION_SEARCH_SCHEMA["name"] == "lattice_session_search"
