"""Tests for gateway/platforms/lattice.py — Lattice SSE adapter."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.lattice import (
    DEFAULT_LATTICE_URL,
    LatticeAdapter,
    check_lattice_requirements,
    get_lattice_public_key,
)
from gateway.session import SessionSource
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner

TEST_PRIVKEY_HEX = "00" * 32
RECIPIENT_HEX = "ab" * 32


class TestLatticeRoutedAuthorization:
    """Lattice-routed Telegram events must not hit user allowlists (group chat_id ≠ user id)."""

    def test_lattice_routed_always_authorized(self):
        gw = GatewayRunner.__new__(GatewayRunner)
        gw.config = GatewayConfig()
        gw.pairing_store = MagicMock()
        gw.pairing_store.is_approved.return_value = False

        source = SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="-1001234567890",
            chat_type="dm",
            user_id=None,
            lattice_routed=True,
        )
        with patch.dict("os.environ", {}, clear=True):
            assert gw._is_user_authorized(source) is True


class TestGatewayRunnerLatticeWiring:
    """Regression: GatewayRunner._create_adapter builds LatticeAdapter (gateway/run.py)."""

    def test_create_adapter_lattice_with_session_target(self, tmp_path):
        lattice_cfg = PlatformConfig(
            enabled=True,
            extra={
                "session_target": {"platform": "telegram", "chat_id": "99"},
            },
        )
        gw = GatewayConfig(
            platforms={Platform.LATTICE: lattice_cfg},
            sessions_dir=tmp_path / "sessions",
        )
        runner = GatewayRunner(gw)
        adapter = runner._create_adapter(Platform.LATTICE, lattice_cfg)
        assert isinstance(adapter, LatticeAdapter)


class TestCheckLatticeRequirements:
    def test_always_true(self):
        assert check_lattice_requirements() is True


class TestLatticeAdapterInit:
    def test_default_url_when_unset(self, monkeypatch):
        monkeypatch.delenv("LATTICE_URL", raising=False)
        cfg = PlatformConfig(enabled=True, extra={})
        adapter = LatticeAdapter(cfg)
        assert adapter._lattice_url == DEFAULT_LATTICE_URL.rstrip("/")

    def test_url_from_extra(self):
        cfg = PlatformConfig(enabled=True, extra={"url": "https://custom.example/"})
        adapter = LatticeAdapter(cfg)
        assert adapter._lattice_url == "https://custom.example"

    def test_topics_from_extra(self):
        cfg = PlatformConfig(
            enabled=True,
            extra={"url": "http://x", "topics": " a , b "},
        )
        adapter = LatticeAdapter(cfg)
        assert adapter._topics == "a , b"


class TestGetLatticePublicKey:
    def test_returns_hex_when_key_in_env(self, monkeypatch):
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        pk = get_lattice_public_key()
        assert pk is not None
        assert len(pk) == 64


class TestLatticeAdapterSend:
    @pytest.mark.asyncio
    async def test_not_connected(self):
        cfg = PlatformConfig(enabled=True, extra={"url": "http://x"})
        adapter = LatticeAdapter(cfg)
        adapter.client = None
        result = await adapter.send("to_key", "hello")
        assert result.success is False
        assert "Not connected" in (result.error or "")

    @pytest.mark.asyncio
    async def test_send_success(self, monkeypatch):
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        cfg = PlatformConfig(enabled=True, extra={"url": "http://lattice.test"})
        adapter = LatticeAdapter(cfg)
        adapter._privkey_hex = TEST_PRIVKEY_HEX

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        adapter.client = mock_client

        result = await adapter.send(RECIPIENT_HEX, "hello there")
        assert result.success is True
        mock_client.post.assert_awaited_once()
        assert mock_client.post.await_args[0][0] == "http://lattice.test/send"

    @pytest.mark.asyncio
    async def test_send_404(self, monkeypatch):
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        cfg = PlatformConfig(enabled=True, extra={"url": "http://lattice.test"})
        adapter = LatticeAdapter(cfg)
        adapter._privkey_hex = TEST_PRIVKEY_HEX

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        adapter.client = mock_client

        result = await adapter.send(RECIPIENT_HEX, "x")
        assert result.success is False
        assert result.error == "Agent not connected"


class TestLatticeAdapterGetChatInfo:
    @pytest.mark.asyncio
    async def test_minimal_info(self):
        cfg = PlatformConfig(enabled=True, extra={"url": "http://x"})
        adapter = LatticeAdapter(cfg)
        info = await adapter.get_chat_info("cid-1")
        assert info == {"chat_id": "cid-1", "type": "dm"}


class TestLatticeAdapterNotifications:
    @pytest.mark.asyncio
    async def test_drops_without_session_target(self, monkeypatch):
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        cfg = PlatformConfig(enabled=True, extra={"url": "http://x"})
        adapter = LatticeAdapter(cfg)

        await adapter._process_notification(
            json.dumps({"body": "hello", "from": "aa" * 32})
        )
        # No crash; notification dropped quietly after log

    @pytest.mark.asyncio
    async def test_routes_to_gateway_runner(self, monkeypatch):
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        cfg = PlatformConfig(
            enabled=True,
            extra={
                "url": "http://x",
                "session_target": {
                    "platform": "telegram",
                    "chat_id": "999",
                },
            },
        )
        adapter = LatticeAdapter(cfg)

        target_handle = AsyncMock()
        target_adapter = MagicMock()
        target_adapter.handle_message = target_handle

        runner = MagicMock()
        runner.adapters = {Platform.TELEGRAM: target_adapter}
        adapter.gateway_runner = runner

        await adapter._process_notification(
            json.dumps(
                {
                    "body": "ping",
                    "from": "bb" * 32,
                    "topic": "alerts",
                }
            )
        )

        target_handle.assert_awaited_once()
        event = target_handle.await_args.args[0]
        assert isinstance(event, MessageEvent)
        assert event.message_type == MessageType.TEXT
        assert "incoming push notification" in event.text
        assert "topic alerts" in event.text
        assert "bb" * 32 in event.text
        assert "ping" in event.text
        assert event.source.lattice_routed is True
        assert event.source.user_id is None
        assert event.source.chat_type == "dm"

    @pytest.mark.asyncio
    async def test_fallback_message_handler(self, monkeypatch):
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        cfg = PlatformConfig(
            enabled=True,
            extra={
                "url": "http://x",
                "session_target": {"platform": "telegram", "chat_id": "1"},
            },
        )
        adapter = LatticeAdapter(cfg)
        adapter.gateway_runner = None
        handler = AsyncMock()
        adapter._message_handler = handler

        await adapter._process_notification(json.dumps({"body": "only"}))

        handler.assert_awaited_once()


class TestDispatchSseEvent:
    @pytest.mark.asyncio
    async def test_connected_parses_json(self, monkeypatch):
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        cfg = PlatformConfig(enabled=True, extra={"url": "http://x"})
        adapter = LatticeAdapter(cfg)

        await adapter._dispatch_sse_event(
            "connected",
            "evt-1",
            json.dumps({"pubkey": "abc123", "topics": ["t1"]}),
        )
        assert adapter._last_event_id == "evt-1"

    @pytest.mark.asyncio
    async def test_notification_delegates(self, monkeypatch):
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        cfg = PlatformConfig(enabled=True, extra={"url": "http://x"})
        adapter = LatticeAdapter(cfg)

        with patch.object(adapter, "_process_notification", new=AsyncMock()) as proc:
            await adapter._dispatch_sse_event("notification", "n1", '{"body":"x"}')
        proc.assert_awaited_once_with('{"body":"x"}')
