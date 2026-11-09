# Public phone entrance on cup

Hub-side of the direct path. The bridge walks `wss://gsm.ta3leam.com/` then `ws://100.113.222.47:8787/` and sends `X-Gsm-Device-Token` on both. Pulling this code and restarting the hub **without** the new environment variables does not change the Tailscale listener.

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
| Device header | `X-Gsm-Device-Token: <secret>` on **every** request | the bridge sends the same header. A missing token is still allowed until `GSM2COMPUTER_TAILSCALE_TOKEN_REQUIRED=1` |
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

Do not put these variables in `gsm2computer-hub.service`. Add them to the drop-in `~/.config/systemd/user/gsm2computer-hub.service.d/cup-private.conf`. Leave Talk and the audio buses on the unit.

```bash
mkdir -p ~/.config/systemd/user/gsm2computer-hub.service.d
cat > ~/.config/systemd/user/gsm2computer-hub.service.d/cup-private.conf <<'EOF'
[Service]
Environment=GSM2COMPUTER_HUB_HOST=100.113.222.47
Environment=GSM2COMPUTER_HUB_PORT=8787
Environment=GSM2COMPUTER_PUBLIC_ENABLE=1
Environment=GSM2COMPUTER_PUBLIC_HOST=127.0.0.1
Environment=GSM2COMPUTER_PUBLIC_PORT=8790
Environment=GSM2COMPUTER_PUBLIC_HOSTNAME=gsm.ta3leam.com
Environment=GSM2COMPUTER_DEVICE_TOKENS=%h/.config/gsm2computer/device-tokens.json
Environment=GSM2COMPUTER_TAILSCALE_TOKEN_REQUIRED=0
EOF
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

`GSM2COMPUTER_ACME_EMAIL` is required. It is the Let's Encrypt account contact, and it belongs in this env file. Caddy refuses to start when it is empty, because the Caddyfile's `email` line then has no argument. Set it to a mailbox you read. Set the hostname if you did not use `gsm.ta3leam.com`. Leave `GSM2COMPUTER_PUBLIC_BIND=62.83.35.197`. The Caddy unit runs `gsm2computer-caddy-pre.sh` first and will not start if that bind is empty, `0.0.0.0`, or `::`.

The unit sets `StateDirectory=caddy`, `XDG_DATA_HOME=/var/lib/caddy`, and `XDG_CONFIG_HOME=/var/lib/caddy`. Caddy 2.6 appends `/caddy` to those variables, so the account and certificates live in `/var/lib/caddy/caddy`.

#### One-time copy off `/caddy`

The entrance is already live. The previous unit set no `HOME` and no `XDG_*`, and a system unit's working directory is `/`, so Caddy stored the account and the certificate in `/caddy`. The new unit reads `/var/lib/caddy/caddy`. `/caddy` stays where it is. The old unit still uses it, so putting that unit back does not move either tree.

`deploy/cup/migrate-caddy-state.sh apply` saves the fingerprint at `/var/lib/caddy/gsm-cert.before` and the installed unit at `/var/lib/caddy/gsm2computer-caddy.service.bak`. Neither file is under `/tmp`. It then stops Caddy, runs `cp -a /caddy/. /var/lib/caddy/caddy/`, installs this repo's unit, `daemon-reload`, and `start`. If `/var/lib/caddy/caddy` already has files that differ from `/caddy`, apply exits 1 before the stop and leaves the current unit running. A copy that matches, or an empty destination, is fine. `cp -a` keeps owner, group, and mode.

If the copy, the unit install, `daemon-reload`, `start`, or the fingerprint check fails, the script does not depend on `set -e`. It stops Caddy, writes the backed-up unit back, `daemon-reload`, and starts that unit. A second apply, once the new unit is installed and the copy matches, does nothing.

```bash
sudo deploy/cup/migrate-caddy-state.sh apply
sudo journalctl -u gsm2computer-caddy.service -n 50 --no-pager
```

The script exits 0 only when the fingerprint matches. The journal after the start does not show a new issuance. Leave `/caddy` in place. After a week of renewals that keep using the certificate under `/var/lib/caddy/caddy`, delete `/caddy` by hand.

On a first install there is no `/caddy` directory. Install the unit and start it. The log shows a certificate obtained for the hostname.

```bash
sudo install -m 0755 deploy/cup/gsm2computer-caddy-pre.sh /usr/local/sbin/gsm2computer-caddy-pre.sh
sudo cp deploy/cup/gsm2computer-caddy.service /etc/systemd/system/gsm2computer-caddy.service
sudo systemctl daemon-reload
sudo systemctl enable --now gsm2computer-caddy.service
sudo journalctl -u gsm2computer-caddy.service -n 50 --no-pager
```

`ss -ltnp 'sport = :443'` should show Caddy on `62.83.35.197:443` and Tailscale Serve still on the tailnet address.

Then, from a machine that is **not** on Tailscale:

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

Cup already has `/etc/nftables.conf` with `flush ruleset` and `table inet filter`. That filter drops public inbound except SSH 22, UDP 41641, UDP 41642 (the second Tailscale, portal/Funnel), and ICMP, and it allows RustDesk plus `8787`/`8449` on `tailscale0`. A second `inet` table cannot open TCP 443: a packet has to survive every base chain, the host filter still drops 443, and a public-only allow list that names only 41641 would drop 41642. `flush ruleset` on boot also deletes any table a later unit added if it loses the race.

The drop-in does not flush and does not create a table. It inserts one rule into the existing input chain: `tcp dport 443` on the public NIC. 41641 and 41642 stay where they are.

```bash
ip -o -4 addr show | awk '/62.83.35.197/ {print $2}'
```

```bash
cd ~/gsm2computer
sudo GSM2COMPUTER_PUBLIC_IF=<nic> GSM2COMPUTER_NFT_ROLLBACK_S=300 \
  deploy/cup/gsm2computer-nft.sh check
sudo GSM2COMPUTER_PUBLIC_IF=<nic> GSM2COMPUTER_NFT_ROLLBACK_S=300 \
  deploy/cup/gsm2computer-nft.sh apply
```

`apply` inserts the live rule and arms a timer (default 180 seconds, 300 in the command above). The session you applied from is already established, so it survives. Before the timer fires:

1. Open a **new** SSH session. If it fails, run `deploy/cup/gsm2computer-nft.sh revert` from the original session, or wait for the timer.
2. From outside the tailnet: `curl -sS -o /dev/null -w '%{http_code}\n' https://gsm.ta3leam.com/health` is `401`, and with the token it is `200`.
3. From outside: RustDesk ports and `8790` and `8787` on the public IP do not accept. `tailscale0` still reaches the hub and RustDesk. UDP 41642 still accepts.
4. `sudo deploy/cup/gsm2computer-nft.sh confirm`

`confirm` appends an include of `/etc/nftables.d/gsm2computer-public.nft` to `/etc/nftables.conf` and enables `gsm2computer-nft.service` before it cancels the rollback timer. The unit runs after `nftables.service` and only inserts the rule if it is missing. It has no `ExecStop`. `revert` deletes that rule and the include. It does not flush the host filter.

## Rollback

Do these independently. You do not need to undo all of them.

| What broke | Undo |
|---|---|
| Firewall lockout, timer still running | Wait. It removes the TCP 443 rule. Or from the surviving SSH session: `sudo deploy/cup/gsm2computer-nft.sh revert` |
| Firewall confirmed, want 443 closed | `sudo /usr/local/sbin/gsm2computer-nft.sh revert` (disables the boot unit, deletes the 443 rule, removes the include) |
| Caddy unit after the copy | `sudo deploy/cup/migrate-caddy-state.sh rollback`. Stops Caddy, restores `/var/lib/caddy/gsm2computer-caddy.service.bak`, `daemon-reload`, and starts that unit. It compares `/var/lib/caddy/gsm-cert.before` when that file exists, and still restores the unit when it does not. It does not move `/caddy` or `/var/lib/caddy/caddy`. `apply` runs the same unit restore if start fails or the fingerprint changes. |
| Caddy certificate or proxy, storage not moved | `sudo systemctl disable --now gsm2computer-caddy.service` |
| Public hub socket | Comment out `GSM2COMPUTER_PUBLIC_ENABLE`, `systemctl --user restart gsm2computer-hub.service`. Tailscale `:8787` keeps the previous behavior |
| Bad hub code | `git checkout <previous>` in the deployed checkout, restart the user unit. With `PUBLIC_ENABLE` unset the new code still serves only the Tailscale socket |
| SSH keys-only too strict | `sudo rm /etc/ssh/sshd_config.d/99-gsm2computer-key-only.conf && sudo sshd -t && sudo systemctl reload ssh` |

Netcup's panel firewall, if it is on, has to allow TCP 22, TCP 443, and UDP 41641 and 41642 as well. This drop-in does not replace that panel.

## What this host does not prove

A cloud-agent run of the hub uses loopback sockets, a local Caddy certificate, and an nftables network namespace. It does not reach `62.83.35.197`, Let's Encrypt, or the phone.
