#!/usr/bin/env python3
"""
lattice_notify_user tool — Lattice platform only.

Sends a message to the human user on their main platform (the session_target
configured in the lattice platform's config.yaml entry).  Use this when an
incoming agent notification requires the user's attention or permission.

This tool is gated to the lattice platform via check_fn and is NOT exposed to
any other platform toolset.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

# Module-level imports so tests can patch these via tools.lattice_notify_user_tool.*
try:
    from gateway.config import load_gateway_config, Platform
    from tools.send_message_tool import _handle_send
except Exception:
    load_gateway_config = None  # type: ignore[assignment]
    Platform = None  # type: ignore[assignment]
    _handle_send = None  # type: ignore[assignment]


NOTIFY_USER_SCHEMA = {
    "name": "lattice_notify_user",
    "description": (
        "Send a message to the user on their main platform (e.g. Telegram home channel). "
        "Use this to surface important information from an incoming agent notification, "
        "ask the user for permission before taking an action, or escalate anything that "
        "requires human judgment. The user will see this message in their normal chat."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The message to send to the user.",
            },
        },
        "required": ["message"],
    },
}


def _check_lattice_notify_user() -> bool:
    """Only available when running inside a lattice platform session."""
    return os.getenv("HERMES_SESSION_PLATFORM", "") == "lattice"


def lattice_notify_user_tool(args: dict, **kwargs) -> str:
    """Send a notification to the user on the configured session_target platform."""
    message = (args.get("message") or "").strip()
    if not message:
        return json.dumps({"error": "message is required"}, ensure_ascii=False)

    if load_gateway_config is None:
        return json.dumps({"error": "gateway config not available"}, ensure_ascii=False)

    try:
        config = load_gateway_config()
    except Exception as e:
        return json.dumps(
            {"error": f"Failed to load gateway config: {e}"}, ensure_ascii=False
        )

    lattice_cfg = config.platforms.get(Platform.LATTICE)
    session_target = (
        (lattice_cfg.extra or {}).get("session_target", {}) if lattice_cfg else {}
    )
    target_platform = session_target.get("platform", "")
    target_chat_id = str(session_target.get("chat_id", ""))

    if not target_platform or not target_chat_id:
        return json.dumps(
            {
                "error": "session_target not configured — add it to the lattice platform config"
            },
            ensure_ascii=False,
        )

    return _handle_send(
        {"target": f"{target_platform}:{target_chat_id}", "message": message}
    )


try:
    from tools.registry import registry

    registry.register(
        name="lattice_notify_user",
        toolset="lattice_tools",
        schema=NOTIFY_USER_SCHEMA,
        handler=lattice_notify_user_tool,
        check_fn=_check_lattice_notify_user,
        emoji="🔔",
    )
except Exception:
    pass
