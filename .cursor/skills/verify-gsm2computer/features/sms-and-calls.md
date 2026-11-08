# SMS outbox and call report

## Sub-features

- `POST /portal/api/messages/send` on the Tailscale socket queues an outbound SMS. The portal is not on the public socket.
- `GET /sms/outbox` claims pending items. The public socket requires the device token. The Tailscale socket does not, while the transition flag is off.
- `POST /sms/outbox/{id}/ack` on the public socket completes the item.
- `POST /calls` stores `path` as `public` or `tailscale`. The hub stamps the entrance of the socket that carried the audio. A client-supplied path does not override that.

## How to get to it (user POV)

The portal operator sends a text. The phone polls `/sms/outbox` every 10 seconds and acks. After a bridged call the phone `POST`s `/calls`. The call row's `path` is which entrance carried the audio. The portal call card shows that value once `hub/portal` is rebuilt.

## Driving it with this skill

`drive-entrances.py` queues one SMS on the Tailscale socket, rejects an outbox poll on the public socket with no token, claims it with the token, acks it, then after the public loopback call posts `/calls` with that session id and expects `"path": "public"`.

```bash
.cursor/skills/verify-gsm2computer/scripts/launch-hub.sh
python3 .cursor/skills/verify-gsm2computer/scripts/drive-entrances.py
```

## Gotchas

- Claiming the outbox marks the row `sending`. A second poll returns nothing until something is queued again. Don't expect the item to still be pending after a successful public poll.
- Today's phone posts `/calls` without `session_id` and without `path`. The hub remembers the last finished entrance for 60 seconds (`GSM2COMPUTER_CALL_PATH_STAMP_S`) and uses it when the body has no session id. The drive sends the session id so the stamp is exact.
- `POST /calls` on the public socket also requires the device token. The drive posts it to the Tailscale socket, which is what today's uploader does.
