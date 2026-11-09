# Public TCP 443 drop-in

- Cup already loads `table inet filter` from `/etc/nftables.conf`, and that file starts with `flush ruleset`.
- The drop-in does not create `inet gsm2computer` and does not flush.
- It inserts `tcp dport 443` on the public NIC into the existing input chain.
- UDP 41641 and UDP 41642 stay in the host filter. RustDesk and `8787`/`8449` on `tailscale0` stay there too.
- `apply` arms a rollback timer before the rule is inserted. `confirm` includes the drop-in from `/etc/nftables.conf` and enables the boot unit before cancelling the timer. `revert` deletes the rule and the include.

## How an operator does it

```bash
sudo GSM2COMPUTER_PUBLIC_IF=<nic> deploy/cup/gsm2computer-nft.sh check
sudo GSM2COMPUTER_PUBLIC_IF=<nic> GSM2COMPUTER_NFT_ROLLBACK_S=300 deploy/cup/gsm2computer-nft.sh apply
sudo deploy/cup/gsm2computer-nft.sh confirm
```

Undo: `sudo deploy/cup/gsm2computer-nft.sh revert`, or wait out the timer if `confirm` was not run. Full sequence: [docs/CUP_PUBLIC_ENTRANCE.md](../../../../docs/CUP_PUBLIC_ENTRANCE.md).

## How this skill drives it

```bash
.cursor/skills/verify-gsm2computer/scripts/check-firewall.sh
```

That loads the cup-shaped filter from `deploy/cup/nftables.filter.example.conf` inside netns `gsm2nft-verify`, runs `nft -c`, inserts the 443 rule, checks 41642 is still accepted, checks the host namespace did not gain the rule, reverts, proves the sleep timer removes only the 443 rule, proves a failed `confirm` leaves the timer armed and does not leave the include in place, and proves a successful `confirm` writes the include before it disarms.

## Gotchas

- Do not run `apply` on this VM's host namespace. `check-firewall.sh` is the safe drive.
- `confirm` in this skill uses a stub `systemctl` and writes under the temp state dir. It does not enable a host unit or touch the host nftables.
- The example ruleset is the shape described for cup, not a byte copy of `/etc/nftables.conf`.
