"""Tests for gateway/platforms/lattice.py — Lattice SSE adapter."""

from datetime import datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.lattice import (
    DEFAULT_LATTICE_URL,
    LatticeAdapter,
    check_lattice_requirements,
    get_lattice_public_key,
)
from gateway.session import SessionEntry, SessionSource, build_session_key
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


_HOME_CHAT_ID = "99"
_SESSION_TARGET = {"platform": "telegram", "chat_id": _HOME_CHAT_ID}


def _make_lattice_event(
    text: str = "hello", lattice_sender: str = None
) -> MessageEvent:
    """Create a forwarded Lattice notification event (source = home platform)."""
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id=_HOME_CHAT_ID,
            chat_type="dm",
        ),
        message_id="m1",
        lattice_sender=lattice_sender or ("ab" * 32),
    )


def _make_runner_for_lattice_message_flow() -> GatewayRunner:
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={
            Platform.LATTICE: PlatformConfig(
                enabled=True, extra={"session_target": _SESSION_TARGET}
            )
        }
    )
    adapter = MagicMock()
    adapter.send = AsyncMock()
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)

    session_entry = SessionEntry(
        session_key=build_session_key(_make_lattice_event().source),
        session_id="lattice-session-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store.load_transcript.return_value = []
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.append_to_transcript = MagicMock()
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.update_session = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._show_reasoning = False
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._should_send_voice_reply = lambda *_args, **_kwargs: False
    runner._send_voice_reply = AsyncMock()
    runner._capture_gateway_honcho_if_configured = lambda *args, **kwargs: None
    runner._emit_gateway_run_progress = AsyncMock()
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "ok",
            "messages": [],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )
    return runner


class TestCheckLatticeRequirements:
    def test_always_true(self):
        assert check_lattice_requirements() is True


class TestGatewayRunnerLatticeHomePrompt:
    @pytest.mark.asyncio
    async def test_forwarded_lattice_notification_skips_home_channel_prompt(
        self, monkeypatch
    ):
        """Forwarded Lattice notifications (lattice_sender set) must not trigger the
        'no home channel' prompt — the home channel is already set by definition."""
        import gateway.run as gateway_run

        runner = _make_runner_for_lattice_message_flow()
        event = _make_lattice_event()  # has lattice_sender set

        monkeypatch.delenv("TELEGRAM_HOME_CHANNEL", raising=False)
        monkeypatch.setattr(
            gateway_run,
            "_resolve_runtime_agent_kwargs",
            lambda: {"api_key": "***"},
        )
        monkeypatch.setattr(
            "agent.model_metadata.get_model_context_length",
            lambda *_args, **_kwargs: 100000,
        )

        result = await runner._handle_message(event)

        assert result == "ok"
        sent_messages = [
            call.args[1]
            for call in runner.adapters[Platform.TELEGRAM].send.await_args_list
            if len(call.args) >= 2
        ]
        assert all("No home channel is set" not in msg for msg in sent_messages)

    @pytest.mark.asyncio
    async def test_sethome_on_lattice_platform_returns_session_target_guidance(self):
        """The LATTICE platform source still returns the session_target guidance message."""
        runner = GatewayRunner.__new__(GatewayRunner)
        lattice_event = MessageEvent(
            text="/sethome",
            source=SessionSource(
                platform=Platform.LATTICE,
                chat_id="ab" * 32,
                chat_type="dm",
            ),
        )
        result = await runner._handle_set_home_command(lattice_event)

        assert "does not use `/sethome`" in result
        assert "lattice.session_target" in result


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
    async def test_routes_to_home_session(self, monkeypatch):
        """Notifications are forwarded to the home (main) session, not a LATTICE session."""
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        cfg = PlatformConfig(
            enabled=True,
            extra={"url": "http://x", "session_target": _SESSION_TARGET},
        )
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
        assert event.text == "ping"
        assert event.source.platform == Platform.TELEGRAM
        assert event.source.chat_id == _HOME_CHAT_ID
        assert event.source.user_id is None
        assert event.source.chat_type == "dm"
        assert event.lattice_sender == sender
        assert event.raw_message.get("from") == sender

    @pytest.mark.asyncio
    async def test_anonymous_notification_routes_to_home_session(self, monkeypatch):
        """Notifications without a sender also route to the home session; lattice_sender=None."""
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        cfg = PlatformConfig(
            enabled=True,
            extra={"url": "http://x", "session_target": _SESSION_TARGET},
        )
        adapter = LatticeAdapter(cfg)

        handler = AsyncMock()
        adapter._message_handler = handler

        await adapter._process_notification(json.dumps({"body": "hello"}))

        handler.assert_awaited_once()
        event = handler.await_args.args[0]
        assert event.source.platform == Platform.TELEGRAM
        assert event.source.chat_id == _HOME_CHAT_ID
        assert event.lattice_sender == "SYSTEM"

    @pytest.mark.asyncio
    async def test_drops_silently_without_message_handler(self, monkeypatch):
        """No handler and no crash — notification dropped with a warning log."""
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        cfg = PlatformConfig(
            enabled=True,
            extra={"url": "http://x", "session_target": _SESSION_TARGET},
        )
        adapter = LatticeAdapter(cfg)
        # No _message_handler set

        # Should not raise
        await adapter._process_notification(json.dumps({"body": "only"}))

    @pytest.mark.asyncio
    async def test_session_target_used_for_routing(self, monkeypatch):
        """session_target determines the home platform and chat_id for the forwarded event."""
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        cfg = PlatformConfig(
            enabled=True,
            extra={
                "url": "http://x",
                "session_target": {"platform": "discord", "chat_id": "chan-42"},
            },
        )
        adapter = LatticeAdapter(cfg)

        handler = AsyncMock()
        adapter._message_handler = handler

        await adapter._process_notification(json.dumps({"body": "routed"}))
        handler.assert_awaited_once()
        event = handler.await_args.args[0]
        assert event.source.platform == Platform.DISCORD
        assert event.source.chat_id == "chan-42"

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
