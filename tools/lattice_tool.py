#!/usr/bin/env python3
"""
Lattice Tool -- Agent-to-Agent Messaging

Allows the AI to send messages to other AI agents via the Lattice
agent-to-agent messaging endpoint (/send).

Requires:
- LATTICE_URL env var
- LATTICE_PRIVATE_KEY_HEX env var (64-char hex Ed25519 private key)
"""

import json
import logging
import os
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)


LATTICE_SEND_AGENT_SCHEMA = {
    "name": "lattice_send_agent",
    "description": (
        "Send a message to another AI agent via Lattice agent-to-agent messaging.\n\n"
        "The recipient is identified by their Ed25519 public key (hex). "
        "The recipient must be currently connected to the same Lattice server."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient agent's Ed25519 public key (64-char hex)",
            },
            "body": {
                "type": "string",
                "description": "Message to send",
            },
        },
        "required": ["to", "body"],
    },
}


def _get_post_auth_headers(privkey_hex: str, body: dict) -> dict:
    """Build Lattice auth headers for POST requests. Signs '{body_json};{timestamp}'."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    privkey_bytes = bytes.fromhex(privkey_hex)
    if len(privkey_bytes) != 32:
        raise ValueError("LATTICE_PRIVATE_KEY_HEX must be 64 hex chars (32 bytes)")

    private_key = Ed25519PrivateKey.from_private_bytes(privkey_bytes)
    pubkey_bytes = private_key.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )
    pubkey_hex = pubkey_bytes.hex()

    timestamp = int(time.time())
    body_json = json.dumps(body, separators=(",", ":"))
    payload = f"{body_json};{timestamp}".encode("utf-8")
    signature = private_key.sign(payload)
    sig_hex = signature.hex()

    return {
        "X-Agent-Pubkey": pubkey_hex,
        "X-Timestamp": str(timestamp),
        "X-Signature": sig_hex,
    }


async def lattice_send_agent_tool(args: Dict[str, Any], **kwargs) -> str:
    """Send a message to another agent via Lattice /send."""
    import httpx

    to = args.get("to", "").strip()
    body_text = args.get("body", "").strip()

    if not to:
        return json.dumps({"error": "Missing required parameter: to"})
    if not body_text:
        return json.dumps({"error": "Missing required parameter: body"})

    lattice_url = os.getenv("LATTICE_URL", "").rstrip("/")
    privkey_hex = os.getenv("LATTICE_PRIVATE_KEY_HEX", "").strip()

    if not lattice_url:
        return json.dumps({"error": "LATTICE_URL environment variable is not set"})
    if not privkey_hex:
        return json.dumps({"error": "LATTICE_PRIVATE_KEY_HEX environment variable is not set"})

    body = {"to": to, "body": body_text}
    try:
        headers = {
            "Content-Type": "application/json",
            **_get_post_auth_headers(privkey_hex, body),
        }
    except Exception as e:
        return json.dumps({"error": f"Failed to build auth headers: {e}"})

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{lattice_url}/send", json=body, headers=headers)
            if resp.status_code == 404:
                return json.dumps({"error": "Agent not connected"})
            resp.raise_for_status()
            return json.dumps({"success": True})
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def check_lattice_send_requirements() -> bool:
    """Return True if LATTICE_URL is configured."""
    return bool(os.getenv("LATTICE_URL"))


# Alias for registry check_fn
_check_lattice_send = check_lattice_send_requirements


# --- Registry ---
from tools.registry import registry

registry.register(
    name="lattice_send_agent",
    toolset="lattice",
    schema=LATTICE_SEND_AGENT_SCHEMA,
    handler=lattice_send_agent_tool,
    check_fn=_check_lattice_send,
    is_async=True,
    emoji="🔗",
)
