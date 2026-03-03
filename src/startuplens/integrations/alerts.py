"""Email alert delivery helpers with quiet-hours and priority handling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import structlog

from startuplens.config import Settings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AlertMessage:
    to_email: str
    subject: str
    body_text: str


def in_quiet_hours(now_utc: datetime, start_hour: int, end_hour: int) -> bool:
    hour = now_utc.hour
    if start_hour == end_hour:
        return False
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def send_via_resend(settings: Settings, message: AlertMessage) -> bool:
    """Send an email via Resend API. Returns False when not configured/fails."""
    if not settings.resend_api_key:
        logger.info("resend_not_configured")
        return False

    payload = {
        "from": settings.alert_email_from,
        "to": [message.to_email],
        "subject": message.subject,
        "text": message.body_text,
    }
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
        return True
    except httpx.HTTPError:
        logger.warning("resend_delivery_failed")
        return False


def should_deliver_now(settings: Settings, priority: str) -> bool:
    """Quiet-hours policy: high/critical bypass quiet hours; others are delayed."""
    if priority in {"high", "critical"}:
        return True
    now_utc = datetime.now(UTC)
    return not in_quiet_hours(
        now_utc,
        settings.quiet_hours_start,
        settings.quiet_hours_end,
    )
