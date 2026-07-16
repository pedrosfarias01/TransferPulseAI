"""Slack notifier for TransferPulse AI.

Posts a message to a Slack incoming webhook every time the desk fires an alert.
Uses only the standard library (urllib) so no extra dependency is needed, and
never raises into the pipeline: a Slack outage must not stop the conveyor belt,
so failures are swallowed and reported back as a short reason string.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def _resolve_webhook_url() -> str:
    """Look up the Slack webhook lazily, on every call.

    Order: Streamlit secrets → env var → empty. Reading at call time (not at
    module import) avoids the classic Streamlit Cloud race where a module-level
    ``os.getenv`` fires before the secrets → env mirror is populated.
    """
    try:
        import streamlit as st  # imported lazily so notifier stays testable
        url = st.secrets.get("SLACK_WEBHOOK_URL", "")
        if url:
            return str(url).strip()
    except Exception:
        # No streamlit context, or no secrets.toml — fall through to env.
        pass
    return (os.getenv("SLACK_WEBHOOK_URL") or "").strip()


def _build_blocks(
    market: str, summary: str, raw_post: str, suggested_action: str
) -> dict:
    """Compose the Slack message payload (Block Kit + plain-text fallback)."""
    header = f"🔔 TransferPulse alert · {market}"
    text = (
        f"*{header}*\n"
        f"*Summary:* {summary}\n"
        f"*Raw post:* {raw_post}\n"
        f"*Suggested action:* {suggested_action}"
    )
    return {
        "text": text,  # fallback for notifications / older clients
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": header, "emoji": True},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Market*\n{market}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Suggested action*\n{suggested_action}",
                    },
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Summary*\n{summary}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Raw post*\n>{raw_post}"},
            },
        ],
    }


def send_alert(
    market: str,
    summary: str,
    raw_post: str,
    suggested_action: str,
) -> tuple[bool, str]:
    """Post one alert to the Slack webhook.

    Returns ``(ok, detail)``. Never raises — a Slack failure must not break the
    pipeline, so any error is caught and returned as ``(False, reason)``.
    """
    url = _resolve_webhook_url()
    if not url:
        return False, "no webhook configured (set SLACK_WEBHOOK_URL in secrets or .env)"

    payload = _build_blocks(market, summary, raw_post, suggested_action)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8", "replace")
            if resp.status == 200 and body.strip() == "ok":
                return True, "sent"
            return False, f"HTTP {resp.status}: {body[:120]}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:120]
        return False, f"HTTP {exc.code}: {detail}"
    except (urllib.error.URLError, OSError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
