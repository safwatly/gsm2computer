# Alert webhook delivery and 2026-10-16 incident

The confirmed reason SMS alerts were missed was the sender's one-shot delivery:
HTTP 400 was logged and the alert was discarded. No response body, retry, or
persisted request existed. The receiver's original reason for rejecting those
requests cannot be recovered from the historical hub logs.

Four SMS payloads at roughly 10:26, 11:17, 12:13, and 12:22 ET were reconstructed
from the journal and the deployed SMS formatter. They were 103-246 UTF-8 JSON
bytes, all ASCII, with no control characters or newlines. They contained only
`text`, with no idempotency key. All used application/json, UTF-8 JSON, and the
same Bearer normalization as successful cup-agent events. The 12:22 payload was
replayed unchanged and returned HTTP 200 with success=true. One explicitly
marked synthetic text alert also returned HTTP 200. The direct synthetic test
was performed exactly once.

This rules out consistently invalid JSON, required missing/extra fields, a
fixed payload-size threshold, emoji, control characters, newlines, a consistently
wrong authentication format, and duplicate sender idempotency keys for these
failures. A transient receiver condition is supported by the unchanged replay;
rate limiting, concurrent routine execution, and an upstream fault remain
hypotheses, not established root causes. Historical response bodies are absent.
Do not claim a specific receiver validation rule was fixed.

## Delivery behavior

Alerts commit to a SQLite outbox before returning. A daemon worker sends outside
the hub event loop, leases rows for 60 seconds across processes, and retries all
non-2xx/transport failures after 30, 60, 120, 240, 480, then 900 seconds plus up
to 5 seconds jitter. Pending alerts have no expiry. The hub starts the worker on
startup, so a restart resumes pending delivery even without a new SMS. The
call-end watcher can enqueue using the same public functions and shared outbox.

Default spool: `~/.local/state/gsm2computer/alert-webhook/outbox.sqlite3`.
Override with `GSM2COMPUTER_ALERT_SPOOL`. The directory is created mode 0700 and
the database mode 0600. Do not share this database: it contains SMS text. Each
row retains original JSON, exact transmitted JSON, last status and redacted
response (up to 2048 characters). Delivered rows expire after seven days;
pending rows remain. Credentials and endpoint are read from process environment
at send time and never stored in the database or logged.

Warnings contain status, alert ID, attempt, retry delay, byte count, and a
redacted response excerpt. Request text stays out of the journal. Record the
next actual 400 response to finish identifying the upstream rejection reason.

Text removes invalid Unicode and non-whitespace C0 controls and is bounded to
4096 UTF-8 bytes, retaining the original in the private spool. Arabic, emoji,
newlines and tabs are preserved. Structured event fields and call_ended test
bypass semantics are preserved. No undocumented receiver size limit is assumed.

Delivery is at least once: a crash or timeout after receiver acceptance can
cause a duplicate. No idempotency header is added without a receiver contract.
Persistent configuration errors remain queued with bounded retries and warnings.

## Cup deployment

Cup has additional deployed hub features beyond the portal-rich base. Deploy
the complete tracked alert_webhook.py plus only the matching import and worker
startup additions to the deployed hub.py, preserving its existing SMS/system
call sites and portal. Back up both files, check /health call.busy is false
immediately before changes/restart, restart the user gsm2computer-hub.service,
and check /health ok and call.busy again. Never replace the deployed hub with
an older branch or deploy main hub.py by itself. No Talk, audio, browser, or
WhatsApp changes are required.
