# Public-interface firewall

## Sub-features

- Table `inet gsm2computer` only. The script refuses a rendered file that contains `flush ruleset`.
- On the public interface: TCP 22, TCP 443, UDP 41641, ICMP errors for path MTU. Everything else on that interface is dropped, including RustDesk TCP 21118 and TCP/UDP 21125–21129.
- Any other interface, including `tailscale0`, matches `iifname != "<public>" accept` and is not filtered by this table.
- `apply` arms a rollback timer before the load. `confirm` persists the table and enables `gsm2computer-nft.service`. `revert` restores the previous table, or deletes it if there was none.

## How to get to it (user POV)

On cup, after SSH-from-a-second-session is proven:

```bash
sudo GSM2COMPUTER_PUBLIC_IF=<nic> deploy/cup/gsm2computer-nft.sh check
sudo GSM2COMPUTER_PUBLIC_IF=<nic> GSM2COMPUTER_NFT_ROLLBACK_S=300 deploy/cup/gsm2computer-nft.sh apply
sudo deploy/cup/gsm2computer-nft.sh confirm
```

Undo: `sudo deploy/cup/gsm2computer-nft.sh revert`, or wait out the timer if `confirm` was not run. Full sequence: [docs/CUP_PUBLIC_ENTRANCE.md](../../../../docs/CUP_PUBLIC_ENTRANCE.md).

## Driving it with this skill

```bash
.cursor/skills/verify-gsm2computer/scripts/check-firewall.sh
```

That runs `nft -c`, loads the table inside netns `gsm2nft-verify`, checks the allow and RustDesk drop lines, checks the host namespace did not gain the table, reverts, and checks the table is gone.

## Gotchas

- Do not run `apply` on this VM's host namespace. `check-firewall.sh` is the safe drive. `nft -c` needs root here (`netlink: Operation not permitted` otherwise).
- This kernel has no `dummy` link type, so the netns apply sets `GSM2COMPUTER_NFT_SKIP_LINK_CHECK=1` and does not create `eth0`. On cup the script refuses to apply if `GSM2COMPUTER_PUBLIC_IF` is missing or `ip link show` cannot see it. Leave that check on.
- Sleep-mode rollback is for the netns test (`GSM2COMPUTER_NFT_ALLOW_SLEEP=1`). Cup uses `systemd-run`. A second `apply` cancels the previous timer so two timers cannot both fire.
- `confirm` writes `/etc/nftables.d` and enables a systemd unit. The skill does not run `confirm`.
