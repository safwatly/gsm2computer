# Feature map

Drive the hub with [.cursor/skills/verify-gsm2computer/SKILL.md](../SKILL.md).

| Feature | File | What a person does |
|---|---|---|
| Public and Tailscale entrances | [entrances.md](entrances.md) | Phone dials the hub; operator reads `/health` |
| Bridge path walk (PR #28 contract) | [bridge-paths.md](bridge-paths.md) | Same origin token + WebSocket, session relink, `call.path` |
| Grok Bot mode | [grokbot.md](grokbot.md) | `AUTO_MODE=grokbot` answers; G2 uplink still carries the caller |
| Loopback call audio | [loopback-audio.md](loopback-audio.md) | Simulator or bridge sends audio frames and hears them back |
| SMS outbox and call report | [sms-and-calls.md](sms-and-calls.md) | Phone polls outbound SMS and uploads the call record |
| Public TCP 443 drop-in | [firewall.md](firewall.md) | Operator inserts 443 into cup's existing inet filter, with a rollback timer |
