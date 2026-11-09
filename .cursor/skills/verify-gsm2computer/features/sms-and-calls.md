# SMS outbox and call report

## Sub-features

- `POST /portal/api/messages/send` on the Tailscale socket queues an outbound SMS. The portal is not on the public socket.
- `GET /sms/outbox` claims pending items. The public socket requires the device token. The Tailscale socket does not, while the transition flag is off.
- `POST /sms/outbox/{id}/ack` on the public socket completes the item.
- `POST /calls` stores `path` as `public` or `tailscale`. The hub stamps the entrance of the socket that carried the audio. A client-supplied path does not override that.
- Public `POST /sms` stores the message the phone reported. `MODE` and `STATUS` in that body do not change the switchboard. The ops webhook, if set, repeats the message text for a person. It does not run a command. The device token is permission to report ingress, not permission to retarget the audio graph. Tailscale still runs `MODE` / `STATUS` from an SMS body.

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
- A call report is labeled only when its `session_id` is a socket this process accepted. A missing session id, or a session id the hub does not know, is stored with `path` null. A client-supplied `path` is ignored. Today's bridge omits `session_id`, so those rows stay unknown until the bridge sends the id from `session.updated`.
- `POST /calls` on the public socket also requires the device token. The drive posts it to the Tailscale socket, which is what today's uploader does.
