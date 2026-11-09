# Public phone entrance on cup

Hub-side of the direct path. The phone keeps using Tailscale until the bridge client (step 3) sends `X-Gsm-Device-Token`. Pulling this code and restarting the hub **without** the new environment variables does not change the Tailscale listener.

Cup: public `62.83.35.197`, Tailscale `100.113.222.47`. Nothing in this repo already points a DNS name at that public address. Use a grey-cloud name on `ta3leam.com`. These steps use `gsm.ta3leam.com`.

## Why Caddy

The hub speaks the HTTP it already speaks. Caddy is the TLS socket in front of `127.0.0.1:8790`.

- A certificate in the audio process would tie renewal to a hub restart. Caddy reloads certificates on its own.
- Android will not trust a private certificate on a call. Let's Encrypt via TLS-ALPN-01 gives a public certificate and does not need port 80.
- Cloudflare's orange-cloud proxy would add a hop (the problem this entrance exists to avoid) and would hide cup from Let's Encrypt. The A record stays **DNS only**.

The hub does not read `X-Forwarded-*`. Whichever socket accepted the connection is the entrance.

## What the bridge client must do (step 3)

One connection routine, origins in order: public, then Tailscale.

| | Public | Tailscale (today's builds) |
|---|---|---|
| Origin | `https://gsm.ta3leam.com` | `http://100.113.222.47:8787` (or the MagicDNS host) |
| WebSocket | `wss://gsm.ta3leam.com/` | `ws://100.113.222.47:8787/` |
| Device header | `X-Gsm-Device-Token: <secret>` on **every** request | omit until you are ready to require it |
| Ephemeral bearer | still `Authorization: Bearer <POST /token value>` if the current client fetches `/token` | unchanged (`Bearer hub-token`). This is **not** the device secret |

Phone routes, same paths as today:

- `POST /token`
- WebSocket upgrade on `/` (loopback tests use `/loopback`)
- `POST /sms`
- `GET /sms/outbox`
- `POST /sms/outbox/{id}/ack`
- `POST /calls`
- `GET /health`

`session.updated` includes `session.entrance` (`public` or `tailscale`). `/health` reports the live call as `call.path` and `call.entrance` with those two values, and the WebSocket route as `call.route`. Stored call records use `path`.

A wrong device token is `401` on both entrances. A missing device token is `401` on the public entrance. On Tailscale, a missing device token is still allowed while `GSM2COMPUTER_TAILSCALE_TOKEN_REQUIRED` is unset or `0`. Set it to `1` only after the installed bridge sends the header.

Portal, `/admin`, and `/switchboard` are not served on the public entrance.

## Deploy

Do this on cup, from a checkout of the branch you intend to run. These commands assume the hub user is the one that already runs `gsm2computer-hub.service`.

### 1. DNS

In Cloudflare, for `ta3leam.com`:

- A record `gsm` → `62.83.35.197`
- Proxy status: **DNS only** (grey cloud)

Wait until `dig +short gsm.ta3leam.com` prints `62.83.35.197` from cup.

### 2. Device token

```bash
cd ~/gsm2computer
install -d -m 700 ~/.config/gsm2computer
python3 hub/device_auth.py generate moto-g84 \
  --file ~/.config/gsm2computer/device-tokens.json
chmod 600 ~/.config/gsm2computer/device-tokens.json
```

The command prints the secret once. Put it somewhere the step-3 bridge can read. It does not go in the unit file, the Caddyfile, or git.

### 3. Hub listener

Edit the user unit (the installed copy, usually `~/.config/systemd/user/gsm2computer-hub.service`) and set:

```
Environment=GSM2COMPUTER_HUB_HOST=100.113.222.47
Environment=GSM2COMPUTER_HUB_PORT=8787
Environment=GSM2COMPUTER_PUBLIC_ENABLE=1
Environment=GSM2COMPUTER_PUBLIC_HOST=127.0.0.1
Environment=GSM2COMPUTER_PUBLIC_PORT=8790
Environment=GSM2COMPUTER_PUBLIC_HOSTNAME=gsm.ta3leam.com
Environment=GSM2COMPUTER_DEVICE_TOKENS=%h/.config/gsm2computer/device-tokens.json
Environment=GSM2COMPUTER_TAILSCALE_TOKEN_REQUIRED=0
```

Leave Talk and the audio buses as they are.

```bash
systemctl --user daemon-reload
systemctl --user restart gsm2computer-hub.service
systemctl --user status gsm2computer-hub.service --no-pager
curl -sS http://100.113.222.47:8787/health | head
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8790/health
```

The Tailscale `/health` is `200`. The loopback public `/health` without a token is `401`. `ss -ltnp | grep 8790` shows `127.0.0.1` only.

### 4. Caddy

Tailscale Serve already listens on the tailnet address at `:443`. Caddy must bind only `62.83.35.197:443`, including the TLS-ALPN challenge. `GSM2COMPUTER_PUBLIC_BIND` in the env file is that address. An empty value or `0.0.0.0` is not a valid deploy.

```bash
sudo install -d -m 755 /etc/gsm2computer
sudo cp deploy/cup/Caddyfile /etc/gsm2computer/Caddyfile
sudo cp deploy/cup/public-entrance.env.example /etc/gsm2computer/public-entrance.env
sudoedit /etc/gsm2computer/public-entrance.env
```

Set `GSM2COMPUTER_ACME_EMAIL` to a mailbox you read, and the hostname if you did not use `gsm.ta3leam.com`. Leave `GSM2COMPUTER_PUBLIC_BIND=62.83.35.197`. The Caddy unit runs `gsm2computer-caddy-pre.sh` first and will not start if that value is empty, `0.0.0.0`, or `::`.

```bash
sudo install -m 0755 deploy/cup/gsm2computer-caddy-pre.sh /usr/local/sbin/gsm2computer-caddy-pre.sh
sudo cp deploy/cup/gsm2computer-caddy.service /etc/systemd/system/gsm2computer-caddy.service
sudo systemctl daemon-reload
sudo systemctl enable --now gsm2computer-caddy.service
sudo journalctl -u gsm2computer-caddy.service -n 50 --no-pager
```

The log should show a certificate obtained for the hostname. `ss -ltnp 'sport = :443'` should show Caddy on `62.83.35.197:443` and Tailscale Serve still on the tailnet address. Then, from a machine that is **not** on Tailscale:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://gsm.ta3leam.com/health
curl -sS -H "X-Gsm-Device-Token: $TOKEN" https://gsm.ta3leam.com/health
```

The first is `401`. The second is `200` with `"entrances"` containing `public` and `tailscale`.

### 5. SSH stays key-only

In a second terminal, confirm `ssh -o PreferredAuthentications=publickey cup` works **before** this step.

```bash
sudo cp deploy/cup/sshd-key-only.conf /etc/ssh/sshd_config.d/99-gsm2computer-key-only.conf
sudo sshd -t && sudo systemctl reload ssh
```

`reload` keeps the current session. From the second terminal, connect again.

### 6. Firewall

Find the public NIC and Tailscale's UDP port:

```bash
ip -o -4 addr show | awk '/62.83.35.197/ {print $2}'
ss -ulnp | grep tailscaled
```

If the UDP port is not 41641, export `GSM2COMPUTER_TAILSCALE_UDP_PORT`. If SSH is not 22, export `GSM2COMPUTER_SSH_PORT` **before** apply. Apply does not flush other nftables or iptables tables.

```bash
cd ~/gsm2computer
sudo GSM2COMPUTER_PUBLIC_IF=<nic> GSM2COMPUTER_NFT_ROLLBACK_S=300 \
  deploy/cup/gsm2computer-nft.sh check
sudo GSM2COMPUTER_PUBLIC_IF=<nic> GSM2COMPUTER_NFT_ROLLBACK_S=300 \
  deploy/cup/gsm2computer-nft.sh apply
```

`apply` arms a timer (default 180 seconds, 300 in the command above). The session you applied from is already established, so it survives. Before the timer fires:

1. Open a **new** SSH session. If it fails, run `deploy/cup/gsm2computer-nft.sh revert` from the original session, or wait for the timer.
2. From outside the tailnet: `curl -sS -o /dev/null -w '%{http_code}\n' https://gsm.ta3leam.com/health` is `401`, and with the token it is `200`.
3. From outside: RustDesk ports and `8790` and `8787` on the public IP do not accept. `tailscale0` still reaches the hub and RustDesk.
4. `sudo deploy/cup/gsm2computer-nft.sh confirm`

`confirm` writes `/etc/nftables.d/gsm2computer.nft` and enables `gsm2computer-nft.service` before it cancels the rollback timer. The unit does not delete the table on stop. The public interface also allows IPv6 neighbor discovery, so the host's IPv6 neighbors survive cache expiry. `revert` is how the table comes off.

## Rollback

Do these independently. You do not need to undo all of them.

| What broke | Undo |
|---|---|
| Firewall lockout, timer still running | Wait. It restores the previous table. Or from the surviving SSH session: `sudo deploy/cup/gsm2computer-nft.sh revert` |
| Firewall confirmed, want it gone | `sudo /usr/local/sbin/gsm2computer-nft.sh revert` (disables the boot unit and deletes the table) |
| Caddy certificate or proxy | `sudo systemctl disable --now gsm2computer-caddy.service` |
| Public hub socket | Comment out `GSM2COMPUTER_PUBLIC_ENABLE`, `systemctl --user restart gsm2computer-hub.service`. Tailscale `:8787` keeps the previous behavior |
| Bad hub code | `git checkout <previous>` in the deployed checkout, restart the user unit. With `PUBLIC_ENABLE` unset the new code still serves only the Tailscale socket |
| SSH keys-only too strict | `sudo rm /etc/ssh/sshd_config.d/99-gsm2computer-key-only.conf && sudo sshd -t && sudo systemctl reload ssh` |

Netcup's panel firewall, if it is on, has to allow TCP 22, TCP 443, and Tailscale's UDP port as well. This nftables table does not replace that panel.

## What this host does not prove

A cloud-agent run of the hub uses loopback sockets, a local Caddy certificate, and an nftables network namespace. It does not reach `62.83.35.197`, Let's Encrypt, or the phone.
