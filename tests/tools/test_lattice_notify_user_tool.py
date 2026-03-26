"""Tests for tools/lattice_notify_user_tool.py"""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestNotifyUserCheckFn:
    def test_available_on_lattice_platform(self, monkeypatch):
        monkeypatch.setenv("HERMES_SESSION_PLATFORM", "lattice")
        from tools.lattice_notify_user_tool import _check_lattice_notify_user
        assert _check_lattice_notify_user() is True

    def test_unavailable_on_telegram_platform(self, monkeypatch):
        monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
        from tools.lattice_notify_user_tool import _check_lattice_notify_user
        assert _check_lattice_notify_user() is False

    def test_unavailable_when_platform_unset(self, monkeypatch):
        monkeypatch.delenv("HERMES_SESSION_PLATFORM", raising=False)
        from tools.lattice_notify_user_tool import _check_lattice_notify_user
        assert _check_lattice_notify_user() is False


class TestNotifyUserTool:
    def _make_config(self, session_target=None):
        from gateway.config import Platform, PlatformConfig, GatewayConfig
        extra = {}
        if session_target:
            extra["session_target"] = session_target
        lattice_cfg = PlatformConfig(enabled=True, extra=extra)
        config = MagicMock()
        config.platforms = {Platform.LATTICE: lattice_cfg}
        return config

    def test_empty_message_returns_error(self):
        from tools.lattice_notify_user_tool import lattice_notify_user_tool
        result = json.loads(lattice_notify_user_tool({"message": ""}))
        assert "error" in result

    def test_missing_session_target_returns_error(self):
        from tools.lattice_notify_user_tool import lattice_notify_user_tool
        config = self._make_config()  # no session_target
        with patch("tools.lattice_notify_user_tool.load_gateway_config", return_value=config):
            result = json.loads(lattice_notify_user_tool({"message": "hello"}))
        assert "error" in result
        assert "session_target" in result["error"]

    def test_sends_to_session_target(self):
        from tools.lattice_notify_user_tool import lattice_notify_user_tool
        config = self._make_config({"platform": "telegram", "chat_id": "999"})
        sent_args = []

        def fake_handle_send(args):
            sent_args.append(args)
            return json.dumps({"success": True})

        with patch("tools.lattice_notify_user_tool.load_gateway_config", return_value=config):
            with patch("tools.lattice_notify_user_tool._handle_send", side_effect=fake_handle_send):
                result = json.loads(lattice_notify_user_tool({"message": "Deploy failed!"}))

        assert len(sent_args) == 1
        assert sent_args[0]["target"] == "telegram:999"
        assert sent_args[0]["message"] == "Deploy failed!"
        assert result["success"] is True

    def test_schema_has_required_message_field(self):
        from tools.lattice_notify_user_tool import NOTIFY_USER_SCHEMA
        assert NOTIFY_USER_SCHEMA["name"] == "lattice_notify_user"
        assert "message" in NOTIFY_USER_SCHEMA["parameters"]["properties"]
        assert NOTIFY_USER_SCHEMA["parameters"]["required"] == ["message"]
