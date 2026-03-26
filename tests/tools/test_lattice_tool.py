"""Tests for tools/lattice_tool.py."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.lattice_tool import (
    check_lattice_requirements,
    check_lattice_send_requirements,
    lattice_get_pubkey_tool,
    lattice_send_tool,
)

TEST_PRIVKEY_HEX = "00" * 32
RECIPIENT_HEX = "ab" * 32


class TestCheckLatticeRequirements:
    def test_true_when_lattice_url_set(self, monkeypatch):
        monkeypatch.setenv("LATTICE_URL", "https://example.com")
        assert check_lattice_requirements() is True

    def test_false_when_unset(self, monkeypatch):
        monkeypatch.delenv("LATTICE_URL", raising=False)
        assert check_lattice_requirements() is False


class TestCheckLatticeSendRequirements:
    def test_false_during_lattice_sessions(self, monkeypatch):
        monkeypatch.setenv("LATTICE_URL", "https://example.com")
        monkeypatch.setenv("HERMES_SESSION_PLATFORM", "lattice")
        assert check_lattice_send_requirements() is False

    def test_true_on_other_platforms_when_configured(self, monkeypatch):
        monkeypatch.setenv("LATTICE_URL", "https://example.com")
        monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
        assert check_lattice_send_requirements() is True


class TestLatticeSendTool:
    @pytest.mark.asyncio
    async def test_missing_to(self, monkeypatch):
        monkeypatch.setenv("LATTICE_URL", "http://x")
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        out = json.loads(await lattice_send_tool({"to": "", "body": "x"}))
        assert "error" in out

    @pytest.mark.asyncio
    async def test_missing_body(self, monkeypatch):
        monkeypatch.setenv("LATTICE_URL", "http://x")
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        out = json.loads(await lattice_send_tool({"to": RECIPIENT_HEX, "body": ""}))
        assert "error" in out

    @pytest.mark.asyncio
    async def test_no_lattice_url(self, monkeypatch):
        monkeypatch.delenv("LATTICE_URL", raising=False)
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)
        out = json.loads(await lattice_send_tool({"to": RECIPIENT_HEX, "body": "hi"}))
        assert out.get("error") == "LATTICE_URL environment variable is not set"

    @pytest.mark.asyncio
    async def test_no_private_key(self, monkeypatch):
        monkeypatch.setenv("LATTICE_URL", "http://x")
        monkeypatch.delenv("LATTICE_PRIVATE_KEY_HEX", raising=False)
        out = json.loads(await lattice_send_tool({"to": RECIPIENT_HEX, "body": "hi"}))
        assert "LATTICE_PRIVATE_KEY_HEX" in out.get("error", "")

    @pytest.mark.asyncio
    async def test_invalid_private_key_length(self, monkeypatch):
        monkeypatch.setenv("LATTICE_URL", "http://x")
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", "ff" * 20)
        out = json.loads(await lattice_send_tool({"to": RECIPIENT_HEX, "body": "hi"}))
        assert "64 hex" in out.get("error", "")

    @pytest.mark.asyncio
    async def test_success_200(self, monkeypatch):
        monkeypatch.setenv("LATTICE_URL", "http://lattice.test")
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            raw = await lattice_send_tool({"to": RECIPIENT_HEX, "body": "hello"})
        out = json.loads(raw)
        assert out == {"success": True}
        mock_client.post.assert_awaited_once()
        call_kw = mock_client.post.await_args
        assert call_kw[0][0] == "http://lattice.test/send"

    @pytest.mark.asyncio
    async def test_agent_not_connected_404(self, monkeypatch):
        monkeypatch.setenv("LATTICE_URL", "http://lattice.test")
        monkeypatch.setenv("LATTICE_PRIVATE_KEY_HEX", TEST_PRIVKEY_HEX)

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            out = json.loads(
                await lattice_send_tool({"to": RECIPIENT_HEX, "body": "hello"})
            )
        assert out.get("error") == "Agent not connected"


class TestLatticeGetPubkeyTool:
    @pytest.mark.asyncio
    async def test_returns_pubkey(self):
        with patch(
            "gateway.platforms.lattice.get_lattice_public_key",
            return_value="deadbeef" * 8,
        ):
            out = json.loads(await lattice_get_pubkey_tool({}))
        assert out == {"pubkey": "deadbeef" * 8}

    @pytest.mark.asyncio
    async def test_error_when_no_pubkey(self):
        with patch(
            "gateway.platforms.lattice.get_lattice_public_key",
            return_value=None,
        ):
            out = json.loads(await lattice_get_pubkey_tool({}))
        assert "error" in out


class TestLatticeToolRegistry:
    """Ensure tools stay registered (import side effects)."""

    def test_tools_registered(self):
        import tools.lattice_tool  # noqa: F401 — register tools
        from tools.registry import registry

        names = registry.get_all_tool_names()
        assert "lattice_send" in names
        assert "lattice_get_pubkey" in names
