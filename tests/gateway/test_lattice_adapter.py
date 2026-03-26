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
                }
            )
        )

        target_handle.assert_awaited_once()
        event = target_handle.await_args.args[0]
        assert isinstance(event, MessageEvent)
        assert event.message_type == MessageType.TEXT
        pk = "bb" * 32
        assert event.text == f"[from agent {pk}]\nping"
        assert event.raw_message.get("from") == pk
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
        routed = handler.await_args.args[0]
        assert routed.text == "only"


class TestLatticeNotificationBackground:
    """Background autonomous processing of Lattice push notifications."""

    def _make_lattice_routed_event(self) -> MessageEvent:
        source = SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="999",
            chat_type="dm",
            user_id=None,
            lattice_routed=True,
        )
        return MessageEvent(
            text="server CPU at 95%",
            message_type=MessageType.TEXT,
            source=source,
        )

    @pytest.mark.asyncio
    async def test_lattice_routed_goes_to_background_not_main_thread(self, tmp_path):
        """lattice_routed events must be redirected to background processing."""
        from gateway.config import GatewayConfig, PlatformConfig
        gw_config = GatewayConfig(sessions_dir=tmp_path / "sessions")
        runner = GatewayRunner(gw_config)

        bg_called_with = []

        async def fake_bg(ev):
            bg_called_with.append(ev)

        runner._handle_lattice_notification_background = fake_bg

        event = self._make_lattice_routed_event()
        result = await runner._handle_message(event)

        assert result is None
        # Give the create_task a chance to run
        import asyncio
        await asyncio.sleep(0)
        assert len(bg_called_with) == 1
        assert bg_called_with[0] is event

    @pytest.mark.asyncio
    async def test_notify_user_prefix_delivers_message(self, tmp_path):
        """[NOTIFY_USER] prefix causes adapter.send with the stripped message."""
        from gateway.config import GatewayConfig
        gw_config = GatewayConfig(sessions_dir=tmp_path / "sessions")
        runner = GatewayRunner(gw_config)

        mock_adapter = MagicMock()
        mock_adapter.send = AsyncMock()
        runner.adapters[Platform.TELEGRAM] = mock_adapter

        event = self._make_lattice_routed_event()

        with patch("run_agent.AIAgent") as MockAgent:
            mock_instance = MagicMock()
            mock_instance.run_conversation.return_value = {
                "final_response": "[NOTIFY_USER]\nHey, the deployment failed!"
            }
            MockAgent.return_value = mock_instance

            with patch("gateway.run._resolve_runtime_agent_kwargs", return_value={"api_key": "k"}):
                with patch("gateway.run._resolve_gateway_model", return_value="test-model"):
                    with patch.object(runner, "_resolve_turn_agent_config", return_value={"model": "test-model", "runtime": {"api_key": "k"}}):
                        with patch.object(runner, "_load_reasoning_config", return_value={}):
                            with patch("asyncio.get_event_loop") as mock_loop:
                                mock_loop.return_value.run_in_executor = AsyncMock(
                                    return_value={"final_response": "[NOTIFY_USER]\nHey, the deployment failed!"}
                                )
                                await runner._handle_lattice_notification_background(event)

        mock_adapter.send.assert_awaited_once()
        call_kwargs = mock_adapter.send.await_args
        sent_content = call_kwargs.kwargs.get("content")
        assert sent_content == "Hey, the deployment failed!"

    @pytest.mark.asyncio
    async def test_silent_processing_sends_nothing(self, tmp_path):
        """No prefix means agent handled it silently — adapter.send must NOT be called."""
        from gateway.config import GatewayConfig
        gw_config = GatewayConfig(sessions_dir=tmp_path / "sessions")
        runner = GatewayRunner(gw_config)

        mock_adapter = MagicMock()
        mock_adapter.send = AsyncMock()
        runner.adapters[Platform.TELEGRAM] = mock_adapter

        event = self._make_lattice_routed_event()

        with patch("gateway.run._resolve_runtime_agent_kwargs", return_value={"api_key": "k"}):
            with patch("gateway.run._resolve_gateway_model", return_value="test-model"):
                with patch.object(runner, "_resolve_turn_agent_config", return_value={"model": "test-model", "runtime": {"api_key": "k"}}):
                    with patch.object(runner, "_load_reasoning_config", return_value={}):
                        with patch("asyncio.get_event_loop") as mock_loop:
                            mock_loop.return_value.run_in_executor = AsyncMock(
                                return_value={"final_response": "Processed and replied to sender."}
                            )
                            await runner._handle_lattice_notification_background(event)

        mock_adapter.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_notify_user_sends_nothing(self, tmp_path):
        """[NOTIFY_USER] with empty body must not call adapter.send."""
        from gateway.config import GatewayConfig
        gw_config = GatewayConfig(sessions_dir=tmp_path / "sessions")
        runner = GatewayRunner(gw_config)

        mock_adapter = MagicMock()
        mock_adapter.send = AsyncMock()
        runner.adapters[Platform.TELEGRAM] = mock_adapter

        event = self._make_lattice_routed_event()

        with patch("gateway.run._resolve_runtime_agent_kwargs", return_value={"api_key": "k"}):
            with patch("gateway.run._resolve_gateway_model", return_value="test-model"):
                with patch.object(runner, "_resolve_turn_agent_config", return_value={"model": "test-model", "runtime": {"api_key": "k"}}):
                    with patch.object(runner, "_load_reasoning_config", return_value={}):
                        with patch("asyncio.get_event_loop") as mock_loop:
                            mock_loop.return_value.run_in_executor = AsyncMock(
                                return_value={"final_response": "[NOTIFY_USER]\n   "}
                            )
                            await runner._handle_lattice_notification_background(event)

        mock_adapter.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_escalate_reinjects_to_main_thread(self, tmp_path):
        """[ESCALATE] prefix must re-inject into _handle_message with lattice_routed=False."""
        from gateway.config import GatewayConfig
        gw_config = GatewayConfig(sessions_dir=tmp_path / "sessions")
        runner = GatewayRunner(gw_config)

        mock_adapter = MagicMock()
        mock_adapter.send = AsyncMock()
        runner.adapters[Platform.TELEGRAM] = mock_adapter

        event = self._make_lattice_routed_event()
        escalated_events = []

        async def capture_handle_message(ev):
            escalated_events.append(ev)
            return None

        runner._handle_message = capture_handle_message

        with patch("gateway.run._resolve_runtime_agent_kwargs", return_value={"api_key": "k"}):
            with patch("gateway.run._resolve_gateway_model", return_value="test-model"):
                with patch.object(runner, "_resolve_turn_agent_config", return_value={"model": "test-model", "runtime": {"api_key": "k"}}):
                    with patch.object(runner, "_load_reasoning_config", return_value={}):
                        with patch("asyncio.get_event_loop") as mock_loop:
                            mock_loop.return_value.run_in_executor = AsyncMock(
                                return_value={"final_response": "[ESCALATE]\nI need permission to delete the deployment"}
                            )
                            await runner._handle_lattice_notification_background(event)

        assert len(escalated_events) == 1
        esc = escalated_events[0]
        assert esc.source.lattice_routed is False
        assert "delete the deployment" in esc.text

    @pytest.mark.asyncio
    async def test_summary_injected_into_main_session(self, tmp_path):
        """After processing, a summary note is appended to the main session transcript."""
        from gateway.config import GatewayConfig
        from gateway.session import SessionStore, SessionEntry
        from datetime import datetime
        import uuid as _uuid

        gw_config = GatewayConfig(sessions_dir=tmp_path / "sessions")
        runner = GatewayRunner(gw_config)

        mock_adapter = MagicMock()
        mock_adapter.send = AsyncMock()
        runner.adapters[Platform.TELEGRAM] = mock_adapter

        # Set up a fake session store with a pre-existing main session entry
        mock_store = MagicMock()
        mock_store._entries = {}
        existing_session_id = f"20260101_120000_{_uuid.uuid4().hex[:8]}"
        mock_entry = MagicMock()
        mock_entry.session_id = existing_session_id
        mock_store._entries["agent:main:telegram:dm:999"] = mock_entry
        mock_store._ensure_loaded = MagicMock()
        appended = []
        mock_store.append_to_transcript = MagicMock(side_effect=lambda sid, msg, **kw: appended.append((sid, msg)))
        runner.session_store = mock_store

        event = self._make_lattice_routed_event()

        with patch("gateway.run._resolve_runtime_agent_kwargs", return_value={"api_key": "k"}):
            with patch("gateway.run._resolve_gateway_model", return_value="test-model"):
                with patch.object(runner, "_resolve_turn_agent_config", return_value={"model": "test-model", "runtime": {"api_key": "k"}}):
                    with patch.object(runner, "_load_reasoning_config", return_value={}):
                        with patch("asyncio.get_event_loop") as mock_loop:
                            mock_loop.return_value.run_in_executor = AsyncMock(
                                return_value={"final_response": "Checked CPU alert; within acceptable range."}
                            )
                            await runner._handle_lattice_notification_background(event)

        assert len(appended) == 1
        sid, note = appended[0]
        assert sid == existing_session_id
        assert note["role"] == "user"
        assert "[background notification processed]" in note["content"]
        assert "server CPU at 95%" in note["content"]
        assert "silent" in note["content"]


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
