#!/usr/bin/env python3
"""
lattice_session_search tool — available on all platforms except lattice itself.

Searches past Lattice agent-to-agent notification sessions so the user's main
agent can recall what happened in the lattice thread (what notifications came
in, what actions were taken, etc.).

Mirrors session_search but scopes the DB query to sessions with source="lattice".
Handled directly in the run_agent.py agent loop (same pattern as session_search)
so that the session_db is injected without going through registry dispatch.
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

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
    """Available when DB exists AND not running inside a lattice session (avoid self-search)."""
    if os.getenv("HERMES_SESSION_PLATFORM", "") == "lattice":
        return False
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
    if db is None:
        return json.dumps(
            {"success": False, "error": "Session database not available."},
            ensure_ascii=False,
        )

    # Re-use the session_search implementation with source_filter locked to lattice.
    from tools.session_search_tool import session_search as _session_search

    # session_search doesn't expose source_filter directly, so we patch the DB call.
    # We wrap db.search_messages to always inject source_filter=["lattice"].
    class _LatticeScopedDB:
        """Thin proxy that forces source_filter=["lattice"] on search_messages calls."""

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

        def __getattr__(self, name):
            return getattr(self._inner, name)

    scoped_db = _LatticeScopedDB(db)
    return _session_search(
        query=query or "",
        role_filter=None,
        limit=limit,
        db=scoped_db,
        current_session_id=current_session_id,
    )


try:
    from tools.registry import registry

    registry.register(
        name="lattice_session_search",
        toolset="lattice_tools",
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
except Exception:
    pass
