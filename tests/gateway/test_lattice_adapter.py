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

    def test_no_gateway_runner_field(self):
        """gateway_runner injection has been removed — adapter has no such attribute."""
        cfg = PlatformConfig(enabled=True, extra={})
        adapter = LatticeAdapter(cfg)
        assert not hasattr(adapter, "gateway_runner")


class TestGetLatticePublicKey:
    def test_returns_hex_when_key_in_env(self, monkeypatch):
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        pk = get_lattice_public_key()
        assert pk is not None
        assert len(pk) == 64


class TestLatticeAdapterSend:
    @pytest.mark.asyncio
    async def test_send_is_noop(self):
        """send() is a no-op — the gateway must not echo responses back to senders.
        Agents use the lattice_send tool explicitly when they want to reply."""
        cfg = PlatformConfig(enabled=True, extra={"url": "http://lattice.test"})
        adapter = LatticeAdapter(cfg)
        mock_client = MagicMock()
        mock_client.post = AsyncMock()
        adapter.client = mock_client

        result = await adapter.send(RECIPIENT_HEX, "hello there")
        assert result.success is True
        mock_client.post.assert_not_awaited()


class TestLatticeAdapterGetChatInfo:
    @pytest.mark.asyncio
    async def test_minimal_info(self):
        cfg = PlatformConfig(enabled=True, extra={"url": "http://x"})
        adapter = LatticeAdapter(cfg)
        info = await adapter.get_chat_info("cid-1")
        assert info == {"chat_id": "cid-1", "type": "dm"}


class TestLatticeAdapterNotifications:
    @pytest.mark.asyncio
    async def test_routes_to_message_handler_with_lattice_platform(self, monkeypatch):
        """Notifications are dispatched as LATTICE platform sessions via _message_handler."""
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        cfg = PlatformConfig(enabled=True, extra={"url": "http://x"})
        adapter = LatticeAdapter(cfg)

        handler = AsyncMock()
        adapter._message_handler = handler

        sender = "bb" * 32
        await adapter._process_notification(
            json.dumps({"body": "ping", "from": sender})
        )

        handler.assert_awaited_once()
        event = handler.await_args.args[0]
        assert isinstance(event, MessageEvent)
        assert event.message_type == MessageType.TEXT
        assert event.text == f"[from agent {sender}]\nping"
        assert event.source.platform == Platform.LATTICE
        assert event.source.chat_id == sender
        assert event.source.user_id == sender
        assert event.source.chat_type == "dm"
        assert event.raw_message.get("from") == sender

    @pytest.mark.asyncio
    async def test_anonymous_notification_uses_lattice_chat_id(self, monkeypatch):
        """Notifications without a sender get chat_id='lattice'."""
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        cfg = PlatformConfig(enabled=True, extra={"url": "http://x"})
        adapter = LatticeAdapter(cfg)

        handler = AsyncMock()
        adapter._message_handler = handler

        await adapter._process_notification(json.dumps({"body": "hello"}))

        handler.assert_awaited_once()
        event = handler.await_args.args[0]
        assert event.source.platform == Platform.LATTICE
        assert event.source.chat_id == "lattice"
        assert event.source.user_id is None

    @pytest.mark.asyncio
    async def test_drops_silently_without_message_handler(self, monkeypatch):
        """No handler and no crash — notification dropped with a warning log."""
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        cfg = PlatformConfig(enabled=True, extra={"url": "http://x"})
        adapter = LatticeAdapter(cfg)
        # No _message_handler set

        # Should not raise
        await adapter._process_notification(json.dumps({"body": "only"}))

    @pytest.mark.asyncio
    async def test_no_session_target_required_for_routing(self, monkeypatch):
        """session_target is no longer required for routing (only needed by notify_user tool)."""
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        cfg = PlatformConfig(enabled=True, extra={"url": "http://x"})
        adapter = LatticeAdapter(cfg)

        handler = AsyncMock()
        adapter._message_handler = handler

        await adapter._process_notification(json.dumps({"body": "no target needed"}))
        handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_json_drops_silently(self, monkeypatch):
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        cfg = PlatformConfig(enabled=True, extra={"url": "http://x"})
        adapter = LatticeAdapter(cfg)
        handler = AsyncMock()
        adapter._message_handler = handler

        await adapter._process_notification("not json")
        handler.assert_not_awaited()


class TestLatticeAuthorization:
    """LATTICE platform messages are always pre-authorized (Ed25519 connection auth)."""

    def test_lattice_platform_always_authorized(self):
        gw = GatewayRunner.__new__(GatewayRunner)
        gw.config = GatewayConfig()
        gw.pairing_store = MagicMock()
        gw.pairing_store.is_approved.return_value = False

        source = SessionSource(
            platform=Platform.LATTICE,
            chat_id="ab" * 32,
            chat_type="dm",
        )
        with patch.dict("os.environ", {}, clear=True):
            assert gw._is_user_authorized(source) is True


class TestDispatchSseEvent:
    @pytest.mark.asyncio
    async def test_connected_parses_json(self, monkeypatch):
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        cfg = PlatformConfig(enabled=True, extra={"url": "http://x"})
        adapter = LatticeAdapter(cfg)

        await adapter._dispatch_sse_event(
            "connected",
            "evt-1",
            json.dumps({"pubkey": "abc123"}),
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
