#!/usr/bin/env python3
"""Inbound SMS persist + STATUS/MODE parse (ADR 0006) + Cup ops wake-up."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from portal_store import PortalStore

if TYPE_CHECKING:
    from portal_http import EventBus

# Payload limits after the ``(k/N) `` label. They leave safe headroom below
# GSM-7's 160-character and UCS-2's 70-character single-message limits.
GSM7_PART_BODY_LIMIT = 150
UCS2_PART_BODY_LIMIT = 62
_GSM7_CHARS = set(
    "@£$¥èéùìòÇ\\nØø\\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\\x1bÆæßÉ !\\\"#¤%&'()*+,-./0123456789:;<=>?¡"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà^{}\\[~]|€"
)


def is_gsm7(text: str) -> bool:
    """Return whether *text* can use the GSM-7 alphabet."""
    return all(char in _GSM7_CHARS for char in text)


def split_outbound_sms(body: str) -> list[str]:
    """Split oversized text at word boundaries and number every part."""
    text = str(body)
    limit = GSM7_PART_BODY_LIMIT if is_gsm7(text) else UCS2_PART_BODY_LIMIT
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind(" ", 0, limit + 1)
        if cut <= 0:
            chunks.append(remaining[:limit])
            remaining = remaining[limit:]
        else:
            chunks.append(remaining[:cut])
            remaining = remaining[cut + 1:]
    total = len(chunks)
    return [f"({index}/{total}) {chunk}" for index, chunk in enumerate(chunks, start=1)]

# Default Safwat ops MSISDN; override/extend via GSM2COMPUTER_OPS_SMS_NUMBERS
# (comma-separated; any common formatting accepted).
_DEFAULT_OPS_NUMBERS = ("+201280043725",)

_SYSTEM_DOWN_RE = re.compile(
    r"(?i)(\bsys(?:tem)?\s*down\b|\bdown\s*again\b|\bstill\s*down\b|"
    r"\bstuck\b|\bbusy\b|\bportal\b|\bvoip\b|"
    r"\bvoice\s*call\b|\bcutte?d\b|\bwake\s*cup\b|\balert\b)"
)


def normalize_msisdn(raw: str) -> str:
    """Normalize to +E.164-ish. Egyptian national 0XXXXXXXXXX -> +20XXXXXXXXXX."""
    text = (raw or "").strip()
    if not text:
        return ""
    digits = re.sub(r"\D+", "", text)
    if not digits:
        return ""
    # Egypt mobile national format: 01xxxxxxxxx (11 digits) -> +201xxxxxxxxx
    if digits.startswith("0") and len(digits) == 11:
        digits = "20" + digits[1:]
    return f"+{digits}"


def ops_sms_numbers() -> set[str]:
    raw = (os.environ.get("GSM2COMPUTER_OPS_SMS_NUMBERS") or "").strip()
    parts = [p.strip() for p in raw.split(",")] if raw else list(_DEFAULT_OPS_NUMBERS)
    out: set[str] = set()
    for part in parts:
        if not part:
            continue
        norm = normalize_msisdn(part)
        if not norm:
            continue
        out.add(norm)
        out.add(re.sub(r"\D+", "", norm))
    return out


def is_ops_sender(from_raw: str) -> bool:
    norm = normalize_msisdn(from_raw)
    if not norm:
        return False
    ops = ops_sms_numbers()
    digits = re.sub(r"\D+", "", norm)
    return norm in ops or digits in ops or f"+{digits}" in ops


def looks_like_system_down(body: str) -> bool:
    text = (body or "").strip()
    if not text:
        return False
    if text.upper() == "STATUS":
        return True
    return bool(_SYSTEM_DOWN_RE.search(text))


def should_wake_cup(from_raw: str, body: str) -> bool:
    """Wake Cup on any SMS from an ops number, or STATUS/system-down from anyone."""
    if is_ops_sender(from_raw):
        return True
    return looks_like_system_down(body)


def inbound_sms_routes(from_raw: str, body: str, *, sms_webhook: bool) -> tuple[bool, bool]:
    """Return (sms_webhook, ops_alert) for one non-duplicate inbound SMS.

    With the SMS webhook configured, every message goes there. A system-down
    body, including STATUS, is also an ops alert. An ops-number sender that
    is not system-down is SMS-only.

    With the SMS webhook unset, only should_wake_cup messages are an ops
    alert, and nothing is sent to the SMS webhook.
    """
    if sms_webhook:
        return True, looks_like_system_down(body)
    return False, should_wake_cup(from_raw, body)


def format_ops_sms_alert(from_raw: str, body: str, received_at: str) -> str:
    preview = (body or "").strip().replace("\n", " ")
    if len(preview) > 280:
        preview = preview[:277] + "..."
    return (
        f"ops SMS wake-up from {from_raw or 'unknown'}: {preview or '(empty)'} "
        f"(receivedAt={received_at})"
    )


def parse_sms_command(body: str) -> Optional[str]:
    text = (body or "").strip()
    if not text:
        return None
    upper = text.upper()
    if upper == "STATUS":
        return "status"
    match = re.match(r"^MODE\s+(\S+)", upper)
    if match:
        return match.group(1).lower()
    return None


def ingest_inbound_sms(
    store: PortalStore,
    events: EventBus,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], Optional[str]]:
    """Persist inbound SMS and return (log_entry, switchboard command)."""
    received = payload.get("receivedAt") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    log_entry = {
        "from": payload.get("from"),
        "body": payload.get("body"),
        "receivedAt": received,
    }
    from_raw = str(payload.get("from") or "")
    body_text = str(payload.get("body") or "")
    client_id = str(payload.get("clientId") or "").strip()[:80]
    if from_raw.strip():
        _msg, created = store.add_inbound_message(
            from_raw,
            body_text,
            ts=str(received),
            status="received",
            client_id=client_id or None,
        )
        if not created:
            log_entry["duplicate"] = True
            return log_entry, None
        events.publish("message", _msg)
    return log_entry, parse_sms_command(body_text)


def fanout_inbound_sms(log_entry: dict[str, Any]) -> None:
    """Route one non-duplicate inbound SMS the way POST /sms does.

    With GSM2COMPUTER_SMS_WEBHOOK_URL set, every message is an sms_in event
    and a system-down body (including STATUS) is also an ops alert. With the
    URL unset, only should_wake_cup messages go to the ops webhook.
    """
    from alert_webhook import post_sms_event, post_system_alert, sms_webhook_configured

    sender = str(log_entry.get("from") or "")
    body_text = str(log_entry.get("body") or "")
    received_at = str(log_entry.get("receivedAt") or "")
    to_sms, to_ops = inbound_sms_routes(
        sender,
        body_text,
        sms_webhook=sms_webhook_configured(),
    )
    if not (to_sms or to_ops):
        return
    text = format_ops_sms_alert(sender, body_text, received_at)
    if to_sms:
        post_sms_event(text, sender=sender, body=body_text, received_at=received_at)
    if to_ops:
        post_system_alert(text)
