"""Tests for tools/lattice_notify_user_tool.py"""

import json
from types import SimpleNamespace
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
    def _make_config(self, session_target=None, home_channels=None):
        from gateway.config import Platform, PlatformConfig
        extra = {}
        if session_target:
            extra["session_target"] = session_target
        lattice_cfg = PlatformConfig(enabled=True, extra=extra)
        config = MagicMock()
        config.platforms = {Platform.LATTICE: lattice_cfg}
        home_channels = home_channels or {}
        for platform, chat_id in home_channels.items():
            config.platforms[platform] = PlatformConfig(enabled=True, extra={})
        config.get_home_channel = lambda platform: (
            SimpleNamespace(chat_id=str(home_channels[platform]), name="Home")
            if platform in home_channels
            else None
        )
        return config

    def test_empty_message_returns_error(self):
        from tools.lattice_notify_user_tool import lattice_notify_user_tool
        result = json.loads(lattice_notify_user_tool({"message": ""}))
        assert "error" in result

    def test_missing_delivery_target_returns_error(self):
        from tools.lattice_notify_user_tool import lattice_notify_user_tool
        config = self._make_config()
        with patch("tools.lattice_notify_user_tool.load_gateway_config", return_value=config):
            result = json.loads(lattice_notify_user_tool({"message": "hello"}))
        assert "error" in result
        assert "Lattice delivery target" in result["error"]

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

    def test_falls_back_to_telegram_home_channel(self):
        from gateway.config import Platform
        from tools.lattice_notify_user_tool import lattice_notify_user_tool

        config = self._make_config(home_channels={Platform.TELEGRAM: "12345"})
        sent_args = []

        def fake_handle_send(args):
            sent_args.append(args)
            return json.dumps({"success": True})

        with patch("tools.lattice_notify_user_tool.load_gateway_config", return_value=config):
            with patch("tools.lattice_notify_user_tool._handle_send", side_effect=fake_handle_send):
                result = json.loads(lattice_notify_user_tool({"message": "Heads up"}))

        assert len(sent_args) == 1
        assert sent_args[0]["target"] == "telegram:12345"
        assert sent_args[0]["message"] == "Heads up"
        assert result["success"] is True

    def test_session_target_takes_precedence_over_home_channel(self):
        from gateway.config import Platform
        from tools.lattice_notify_user_tool import lattice_notify_user_tool

        config = self._make_config(
            {"platform": "telegram", "chat_id": "999"},
            home_channels={Platform.DISCORD: "777"},
        )
        sent_args = []

        def fake_handle_send(args):
            sent_args.append(args)
            return json.dumps({"success": True})

        with patch("tools.lattice_notify_user_tool.load_gateway_config", return_value=config):
            with patch("tools.lattice_notify_user_tool._handle_send", side_effect=fake_handle_send):
                result = json.loads(lattice_notify_user_tool({"message": "Priority"}))

        assert len(sent_args) == 1
        assert sent_args[0]["target"] == "telegram:999"
        assert result["success"] is True

    def test_schema_has_required_message_field(self):
        from tools.lattice_notify_user_tool import NOTIFY_USER_SCHEMA
        assert NOTIFY_USER_SCHEMA["name"] == "lattice_notify_user"
        assert "message" in NOTIFY_USER_SCHEMA["parameters"]["properties"]
        assert NOTIFY_USER_SCHEMA["parameters"]["required"] == ["message"]
