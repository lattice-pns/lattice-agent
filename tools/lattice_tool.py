#!/usr/bin/env python3
"""
Lattice Tools -- Agent-to-Agent Messaging and Session Search

Allows the AI to send messages to other agents, inspect its own identity
via the Lattice agent-to-agent messaging endpoint, and search past Lattice
notification sessions.

Requires:
- LATTICE_URL env var
- LATTICE_PRIVATE_KEY_HEX env var (64-char hex Ed25519 private key)
"""

import json
import logging
import os
from typing import Any, Dict, Optional

from tools.lattice_auth import get_post_auth_headers
from tools.registry import registry

logger = logging.getLogger(__name__)


# ── lattice_send ──────────────────────────────────────────────────────────────

LATTICE_SEND_SCHEMA = {
    "name": "lattice_send",
    "description": (
        "Send a message to another AI agent via Lattice.\n\n"
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


async def lattice_send_tool(args: Dict[str, Any], **kwargs) -> str:
    """Send a message to another agent via Lattice /send."""
    import httpx
    from hermes_cli.config import get_env_value

    to = args.get("to", "").strip()
    body_text = args.get("body", "").strip()

    if not to:
        return json.dumps({"error": "Missing required parameter: to"})
    if not body_text:
        return json.dumps({"error": "Missing required parameter: body"})

    lattice_url = (get_env_value("LATTICE_URL") or os.getenv("LATTICE_URL", "")).rstrip(
        "/"
    )
    privkey_hex = (
        get_env_value("LATTICE_PRIVATE_KEY_HEX")
        or os.getenv("LATTICE_PRIVATE_KEY_HEX", "")
    ).strip()

    if not lattice_url:
        return json.dumps({"error": "LATTICE_URL environment variable is not set"})
    if not privkey_hex:
        return json.dumps(
            {"error": "LATTICE_PRIVATE_KEY_HEX environment variable is not set"}
        )

    # Normalize key: strip non-hex chars (invisible chars, accidental spaces from paste)
    raw_key_len = len(privkey_hex)
    privkey_hex = "".join(
        c for c in privkey_hex if c in "0123456789abcdefABCDEF"
    ).lower()
    if len(privkey_hex) != 64:
        return json.dumps(
            {
                "error": (
                    f"LATTICE_PRIVATE_KEY_HEX must be exactly 64 hex chars (got {len(privkey_hex)}). "
                    f"Raw length was {raw_key_len}. Check ~/.hermes/.env for truncation."
                )
            }
        )

    body = {"to": to, "body": body_text}
    body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    body_bytes = body_str.encode("utf-8")

    try:
        headers = {
            "Content-Type": "application/json",
            **get_post_auth_headers(privkey_hex, body_str),
        }
    except Exception as e:
        return json.dumps({"error": f"Failed to build auth headers: {e}"})

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{lattice_url}/send", content=body_bytes, headers=headers
            )
            if resp.status_code == 404:
                return json.dumps({"error": "Agent not connected"})
            if resp.status_code == 401:
                pubkey_hex = headers["X-Agent-Pubkey"]
                logger.warning(
                    "Lattice 401: pubkey=%s...%s timestamp=%s body=%r",
                    pubkey_hex[:8],
                    pubkey_hex[-8:],
                    headers["X-Timestamp"],
                    body_str,
                )
            resp.raise_for_status()
            return json.dumps({"success": True})
    except httpx.HTTPStatusError as e:
        err = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
        if e.response.status_code == 401:
            pubkey_hex = headers.get("X-Agent-Pubkey", "")
            err += (
                f" Sender pubkey: {pubkey_hex[:16]}...{pubkey_hex[-8:]}. "
                "Check: same key in ~/.hermes/.env; restart gateway if changed; clock ±30s."
            )
        return json.dumps({"error": err})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── lattice_get_pubkey ────────────────────────────────────────────────────────

LATTICE_GET_PUBKEY_SCHEMA = {
    "name": "lattice_get_pubkey",
    "description": "Return this agent's Lattice Ed25519 public key (hex). Share this with other agents so they can send you messages.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}


async def lattice_get_pubkey_tool(args: Dict[str, Any], **kwargs) -> str:
    """Return this agent's Lattice public key."""
    try:
        from gateway.platforms.lattice import get_lattice_public_key

        pubkey = get_lattice_public_key()
        if pubkey:
            return json.dumps({"pubkey": pubkey})
        return json.dumps(
            {"error": "Could not derive public key. Is LATTICE_PRIVATE_KEY_HEX set?"}
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── lattice_session_search ────────────────────────────────────────────────────

LATTICE_SESSION_SEARCH_SCHEMA = {
    "name": "lattice_session_search",
    "description": (
        "Search past Lattice agent-to-agent notification threads. "
        "Use this to recall what agent notifications have been received, "
        "what actions were taken in response, and what the outcomes were.\n\n"
        "TWO MODES:\n"
        "1. Recent sessions (no query): Call with no arguments to see recent lattice activity.\n"
        "2. Keyword search (with query): Search for specific topics across all lattice sessions.\n\n"
        'Search syntax: keywords, phrases ("exact phrase"), boolean (python NOT java), prefix (deploy*).'
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query — omit to browse recent lattice sessions.",
            },
            "limit": {
                "type": "integer",
                "description": "Max sessions to summarize (default: 3, max: 5).",
                "default": 3,
            },
        },
        "required": [],
    },
}


def check_lattice_session_search_requirements() -> bool:
    """Available on all platforms when the DB exists."""
    try:
        from hermes_state import DEFAULT_DB_PATH

        return DEFAULT_DB_PATH.parent.exists()
    except ImportError:
        return False


def lattice_session_search(
    query: str,
    limit: int = 3,
    db=None,
    current_session_id: Optional[str] = None,
) -> str:
    """
    Search past lattice platform sessions and return focused summaries.

    Delegates to the session_search machinery but pins source_filter to ["lattice"]
    so only lattice-platform sessions are returned.
    """
    logger.info("lattice_session_search called: query=%r limit=%d db=%s", query, limit, type(db).__name__)
    if db is None:
        logger.warning("lattice_session_search: session DB is None — was GatewayRunner._session_db initialized?")
        return json.dumps(
            {"success": False, "error": "Session database not available."},
            ensure_ascii=False,
        )

    from tools.session_search_tool import session_search as _session_search

    class _LatticeScopedDB:
        """Thin proxy that scopes all session queries to source="lattice"."""

        def __init__(self, inner):
            self._inner = inner

        def search_messages(
            self, query, source_filter=None, role_filter=None, limit=20, offset=0
        ):
            return self._inner.search_messages(
                query=query,
                source_filter=["lattice"],
                role_filter=role_filter,
                limit=limit,
                offset=offset,
            )

        def list_sessions_rich(self, source=None, limit=20, offset=0):
            results = self._inner.list_sessions_rich(
                source="lattice", limit=limit, offset=offset
            )
            logger.info("lattice_session_search: list_sessions_rich returned %d sessions", len(results))
            return results

        def __getattr__(self, name):
            return getattr(self._inner, name)

    scoped_db = _LatticeScopedDB(db)
    logger.info("lattice_session_search: delegating to session_search (query=%r)", query or "")
    return _session_search(
        query=query or "",
        role_filter=None,
        limit=limit,
        db=scoped_db,
        current_session_id=current_session_id,
    )


# ── requirements check ────────────────────────────────────────────────────────


def check_lattice_requirements() -> bool:
    """Return True if LATTICE_URL is configured."""
    try:
        from hermes_cli.config import get_env_value

        return bool(get_env_value("LATTICE_URL") or os.getenv("LATTICE_URL"))
    except ImportError:
        return bool(os.getenv("LATTICE_URL"))


# ── Registry ──────────────────────────────────────────────────────────────────

registry.register(
    name="lattice_send",
    toolset="lattice",
    schema=LATTICE_SEND_SCHEMA,
    handler=lattice_send_tool,
    check_fn=check_lattice_requirements,
    is_async=True,
    emoji="🔗",
)

registry.register(
    name="lattice_get_pubkey",
    toolset="lattice",
    schema=LATTICE_GET_PUBKEY_SCHEMA,
    handler=lattice_get_pubkey_tool,
    check_fn=check_lattice_requirements,
    is_async=True,
    emoji="🔑",
)

registry.register(
    name="lattice_session_search",
    toolset="lattice",
    schema=LATTICE_SESSION_SEARCH_SCHEMA,
    handler=lambda args, **kw: lattice_session_search(
        query=args.get("query") or "",
        limit=args.get("limit", 3),
        db=kw.get("db"),
        current_session_id=kw.get("current_session_id"),
    ),
    check_fn=check_lattice_session_search_requirements,
    emoji="🔎",
)
