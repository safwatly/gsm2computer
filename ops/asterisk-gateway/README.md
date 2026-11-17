# Asterisk gateway (EC25-E on a Pi)

A second way to put Safwat's Orange Egypt number on the hub. The Moto g84 path stays the default. Nothing here runs until `GSM2COMPUTER_ASTERISK_GATEWAY` is turned on at cutover.

```
caller ──GSM──► EC25-E ──USB audio──► Asterisk on the Pi ──AudioSocket TCP──► hub on cup
                                                                              │
                                                                              ▼
                                                                         phone WebSocket
                                                                         (same slot, patchbay, agent)
```

Why AudioSocket, not SIP: the modem already gives 8 kHz signed-linear PCM, and AudioSocket carries that PCM over one TCP connection. SIP or ARI externalMedia would add RTP and another daemon for a single line. AMI (also TCP) is only for originate, SMS, and the modem CLI. The reasoning is in [ADR 0009](../../docs/adr/0009-asterisk-gsm-gateway.md).

The hub opens the normal phone WebSocket for each AudioSocket call. `/health` `call.busy` is that slot. A second GSM leg gets `Hangup(17)` on its own channel. The live call is not hung up.

## What to buy

- **Quectel EC25-E**, voice firmware, USB dongle or mini-PCIe in a USB enclosure with a SIM slot. Not an EC25-A / AF / data-only SKU. EC25-E covers the Orange Egypt 3G bands the Moto uses today (UMTS B1 and B8) and LTE B3/B7. This path is CS voice, the same 3G voice as the handset. It does not enable VoLTE.
- A **LTE antenna** if the dongle does not include one. Voice will not come up on a bare module.
- **Raspberry Pi 4 (2 GB or more) or Pi 5**, official power supply, 16 GB microSD.
- A **powered USB hub** if the dongle browns out on the Pi's USB port. The EC25 can draw more than that port likes.
- The **Orange SIM from the Moto**, PIN disabled.

## Flash and install the Pi

1. Raspberry Pi Imager → Raspberry Pi OS Lite **64-bit**. Enable SSH, create the user, boot on the network in Egypt.
2. Copy this directory to the Pi and run:

   ```bash
   sudo ./install.sh
   ```

   That is the whole install. Running it again is safe: packages stay, the pinned `chan_quectel` commit is not rebuilt, the AMI secret is not rotated. The secret is created once at `/etc/gsm2computer/ami.secret` (mode 600). Do not commit it.

3. Join Tailscale yourself. The install does not:

   ```bash
   /usr/local/sbin/gsm-gateway-tailscale
   ```

   That script only prints the `tailscale up --hostname=gsm-gateway` command. Put the auth key in the shell, not in git.

4. Plug the modem in, SIM inserted, PIN off. Check:

   ```bash
   aplay -l          # a card named Android or similar
   asterisk -rx "quectel show devices"
   ```

   `install.sh` enables UAC (`AT+QCFG="USBCFG"` last flag = 1, then `AT+QPCMV=1,2` on each boot). `AT+QAUDMOD=3` turns the handset DSP off so the PCM is not run through the speaker path. Set `GSM_GATEWAY_QAUDMOD=off` in `/etc/gsm2computer/gateway.env` and rerun the UAC unit if a given firmware sounds better with the DSP left on.

5. From cup, the Pi's AMI port 5038 should accept the `gsm-gateway` user. The manager user is permitted for loopback and the Tailscale CGNAT range `100.64.0.0/10`, and denied for everything else.

`/etc/gsm2computer/gateway.env` points AudioSocket at `hub.mining-ling.ts.net:9092` and SMS/admit at `http://hub.mining-ling.ts.net:8787`. Change the host there if the tailnet name differs, then `systemctl restart asterisk`.

## Hub env (do this at cutover, not before)

`hub.env.example` lists the variables. Put them in the hub unit override the operator already uses. Do not commit the secret. AudioSocket binds the hub's existing listen address (`GSM2COMPUTER_HUB_HOST`) on port 9092, so it stays on the tailscale IP rather than a public socket.

Leave `GSM2COMPUTER_ASTERISK_GATEWAY` unset until the steps below. Unset, the hub does not bind 9092, does not dial, and does not take the SMS outbox. `/gateway/*` is 404. The Moto bridge is unchanged.

Restart the hub with `hub/ops/cutover.sh` once the line is idle. This repo does not edit the deployed tree.

## Cut over from the Moto

1. Confirm the Pi shows the modem registered (`quectel show devices`) and cup can log in to AMI. Place no call yet.
2. Stop the bridge app on the Moto so it stops opening the hub WebSocket and polling `/sms/outbox`. If both poll, they split the outbox.
3. Power the Moto off and move the SIM into the modem.
4. Set `GSM2COMPUTER_ASTERISK_GATEWAY=1` and the AMI host, user, and secret on cup. Restart the hub with `hub/ops/cutover.sh apply` while `/health` shows `call.busy` false.
5. Call the number from another phone. The caller should hear the agent, and the agent should hear the caller. `/health` `call.busy` is true for the duration and `gateway.transport` is `audiosocket`. Hang up. `call.busy` goes false. A `call_ended` event with `source=asterisk_gateway` is posted. The ADB watcher does not see this call; it watches the phone.
6. Send an SMS to the number. It shows up in the portal thread and, when `GSM2COMPUTER_SMS_WEBHOOK_URL` is set, on that webhook. Reply from the portal. The modem sends it (`quectel sms`). The Moto must stay stopped.
7. Outbound call from the hub:

   ```bash
   curl -sS -X POST -H 'Content-Type: application/json' \
     -d '{"to":"+201xxxxxxxxx"}' \
     http://hub.mining-ling.ts.net:8787/gateway/dial
   ```

A second inbound call while one is up is rejected with cause 17. The first call stays up.

## Roll back

1. On cup, remove `GSM2COMPUTER_ASTERISK_GATEWAY` (or set it to `0`). Restart with `hub/ops/cutover.sh`. The hub stops taking AudioSocket and stops leasing the outbox. The phone WebSocket works as before.
2. Move the SIM back to the Moto. Start the bridge app. Confirm it can open the hub (`/health` goes busy on a test call and idle on hangup).
3. Leave the Pi powered. It is not on the call path while the flag is off. Asterisk can keep running.

## Checks without the modem

On a machine with the repo, no hardware:

```bash
GSM2COMPUTER_PORTAL_DB="$(mktemp /tmp/gsm2computer-portal.XXXXXX.sqlite)" \
  python3 -m unittest hub.test_asterisk_gateway -v
```

That drives a fake AMI and a fake AudioSocket through a fake phone WebSocket: audio both ways, a second leg rejected without aborting the live slot, outbound originate, inbound SMS into the webhook spool, and an outbox row sent as `quectel sms`.
