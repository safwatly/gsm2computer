# Feature map

Drive the hub with [.cursor/skills/verify-gsm2computer/SKILL.md](../SKILL.md).

| Feature | File | What a person does |
|---|---|---|
| Public and Tailscale entrances | [entrances.md](entrances.md) | Phone dials the hub; operator reads `/health` |
| Loopback call audio | [loopback-audio.md](loopback-audio.md) | Simulator or bridge sends audio frames and hears them back |
| SMS outbox and call report | [sms-and-calls.md](sms-and-calls.md) | Phone polls outbound SMS and uploads the call record |
| Public-interface firewall | [firewall.md](firewall.md) | Operator applies nftables on cup with a rollback timer |
