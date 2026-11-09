---
name: verify-gsm2computer
description: >-
  Verify the gsm2computer hub and the bridge's ordered hub walk: public and
  Tailscale phone entrances, the shared device-token check, call.path on
  /health and call reports, Grok Bot mode, G2 uplink, loopback audio, SMS
  outbox, and the cup nftables drop-in. Use before declaring hub, portal,
  SMS, entrance, grokbot, bridge-path, or firewall work done.
---

# Verify gsm2computer

The hub under `hub/` is the phone-facing audio and SMS process. This skill drives that process on loopback. It does not place a GSM call and it does not SSH to cup.

## Launch

From the repo root:

```bash
.cursor/skills/verify-gsm2computer/scripts/launch-hub.sh
```

The script starts PipeWire (only if `pactl info` fails) and WirePlumber when no session manager is running, creates the `gsm_bus` null sinks with `hub/setup-audio-bus.sh`, generates `moto-g84` in `$GSM2COMPUTER_VERIFY_STATE/device-tokens.json` (default state dir `/tmp/gsm2computer-verify`), and starts `python3 hub/hub.py`. On a machine with no systemd-logind, the launched WirePlumber gets a config that turns the logind plugin off so it stays up and links `pw-cat --target`. Cup has logind and does not need that override.

Ready means `GET http://127.0.0.1:18787/health` returns 200. The public socket is `127.0.0.1:18790`. Talk is off (`GSM2COMPUTER_OPENCLAW_TALK=off`). Ports and the device token are in `/tmp/gsm2computer-verify/runtime.env`.

## Doctor

```bash
.cursor/skills/verify-gsm2computer/scripts/doctor.sh
```

Passes when the hub pid is alive, `/health` on the Tailscale socket lists entrances `public` and `tailscale`, `/health` on the public socket with no token returns 401, and `caddy adapt` of `deploy/cup/Caddyfile` listens only on `62.83.35.197:443` with the ACME challenge bound to that address. It does not open a call.

## Drive

Entrance, token, loopback audio, call report, and outbox (one journey):

```bash
python3 .cursor/skills/verify-gsm2computer/scripts/drive-entrances.py
```

Firewall drop-in, still not installed on the host:

```bash
.cursor/skills/verify-gsm2computer/scripts/check-firewall.sh
```

Bridge connection routine, one call on each entrance (and a failover and a relink). This starts its own hub on ports 18987 and 18990. Grok Bot session fields and G2 uplink are covered by `hub.tests.test_reconcile_interactions`, which stubs PipeWire:

```bash
.cursor/skills/verify-gsm2computer/scripts/drive-bridge-paths.sh
```

Feature map: [features/README.md](features/README.md). How to read which path a call used: [features/bridge-paths.md](features/bridge-paths.md).

## Evidence

`drive-entrances.py` writes `/opt/cursor/artifacts/verify-gsm2computer/entrances.json`.
`drive-bridge-paths.sh` writes `/opt/cursor/artifacts/verify-gsm2computer/bridge-paths.json`.
`check-firewall.sh` writes `/opt/cursor/artifacts/verify-gsm2computer/firewall.txt`.
Hub logs stay in `/tmp/gsm2computer-verify/hub.log`. Override the directory with `GSM2COMPUTER_VERIFY_EVIDENCE`.

The journey is the real hub process: HTTP and a WebSocket client that sends `input_audio_buffer.append` frames. Loopback echo is the hub's own `/loopback` path, which still starts `pw-cat` against `gsm_bus`. PipeWire here is a null sink, not the cup graph. Caddy in the drive uses `tls internal` on `127.0.0.1:18443`. That is not Let's Encrypt and not port 443.

## Cleanup

```bash
.cursor/skills/verify-gsm2computer/scripts/teardown.sh
```

Kills the hub, Caddy, WirePlumber, and PipeWire pids recorded under the state dir, and deletes the `gsm2nft-verify` netns if that firewall run left it. It does not delete the evidence directory and it does not flush host nftables.

## Helpers

| Script | Invocation |
|---|---|
| Launch | `.cursor/skills/verify-gsm2computer/scripts/launch-hub.sh` |
| Doctor | `.cursor/skills/verify-gsm2computer/scripts/doctor.sh` |
| Drive entrances | `python3 .cursor/skills/verify-gsm2computer/scripts/drive-entrances.py` |
| Drive bridge paths | `.cursor/skills/verify-gsm2computer/scripts/drive-bridge-paths.sh` |
| Firewall | `.cursor/skills/verify-gsm2computer/scripts/check-firewall.sh` |
| Teardown | `.cursor/skills/verify-gsm2computer/scripts/teardown.sh` |

## Stand-in versus cup

A passing run on this VM does not show:

- packets to `62.83.35.197` or the Tailscale address `100.113.222.47`
- a Let's Encrypt certificate for `gsm.ta3leam.com` (Caddy's local CA is used, and only if `caddy` is on `PATH`)
- nftables on the host's public NIC (the drop-in is inserted inside a network namespace that already holds the cup-shaped filter; this VM cannot create a `dummy` interface, so the netns apply sets `GSM2COMPUTER_NFT_SKIP_LINK_CHECK=1`)
- a rollback timer via `systemd-run` (the netns test uses sleep mode)
- GSM audio, OpenClaw Talk, or the rooted phone
- WirePlumber as cup runs it (this VM has no systemd-logind, so the launcher disables the logind plugin; without that, WirePlumber exits and `pw-cat --target` never links)
- Grok Bot / patchbay / Suzie (the grokbot test stubs `patchbay`)
- the physical phone. `drive-bridge-paths.sh` runs the bridge's `HubConnectionRoutine` and `HubCallDialer` on this VM against loopback sockets. It does not install an APK or place a GSM call.

Cup deploy and rollback: [docs/CUP_PUBLIC_ENTRANCE.md](../../../docs/CUP_PUBLIC_ENTRANCE.md) and [docs/CUP_HUB_CUTOVER.md](../../../docs/CUP_HUB_CUTOVER.md).
