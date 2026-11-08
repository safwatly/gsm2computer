#!/usr/bin/env bash
# Render, syntax-check, apply, and revert the cup nftables table inside a netns.
# Does not install the table on the host. Needs root (sudo).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../../../.." && pwd)
EVIDENCE=${GSM2COMPUTER_VERIFY_EVIDENCE:-/opt/cursor/artifacts/verify-gsm2computer}
mkdir -p "$EVIDENCE"
OUT=$EVIDENCE/firewall.txt
NS=gsm2nft-verify
STATE=$(mktemp -d /tmp/gsm-nft-XXXX)
chmod 777 "$STATE"
cleanup_ns() { sudo ip netns del "$NS" 2>/dev/null || true; }
{
  trap cleanup_ns EXIT
  echo "=== nft -c (not applied) ==="
  sudo GSM2COMPUTER_PUBLIC_IF=eth0 "$ROOT/deploy/cup/gsm2computer-nft.sh" check
  sudo ip netns del "$NS" 2>/dev/null || true
  sudo ip netns add "$NS"
  sudo ip netns exec "$NS" env \
    GSM2COMPUTER_PUBLIC_IF=eth0 \
    GSM2COMPUTER_NFT_STATE="$STATE" \
    GSM2COMPUTER_NFT_PERSIST="$STATE/persist.nft" \
    GSM2COMPUTER_NFT_ALLOW_SLEEP=1 \
    GSM2COMPUTER_NFT_ROLLBACK_MODE=sleep \
    GSM2COMPUTER_NFT_ROLLBACK_S=30 \
    GSM2COMPUTER_NFT_SKIP_LINK_CHECK=1 \
    bash "$ROOT/deploy/cup/gsm2computer-nft.sh" apply
  echo "=== loaded table ==="
  # Capture first. grep -q on a live nft pipe returns SIGPIPE under pipefail.
  table=$(sudo ip netns exec "$NS" nft list table inet gsm2computer)
  printf '%s\n' "$table"
  grep -q 'tcp dport 443 accept' <<<"$table"
  grep -q 'tcp dport 22 accept' <<<"$table"
  grep -q 'udp dport 41641 accept' <<<"$table"
  grep -q 'rustdesk-not-public' <<<"$table"
  grep -q 'iifname != "eth0" accept' <<<"$table"
  if sudo nft list table inet gsm2computer >/dev/null 2>&1; then
    echo "host namespace gained inet gsm2computer; refusing"
    exit 1
  fi
  echo "=== revert ==="
  sudo ip netns exec "$NS" env \
    GSM2COMPUTER_NFT_STATE="$STATE" \
    GSM2COMPUTER_NFT_PERSIST="$STATE/persist.nft" \
    bash "$ROOT/deploy/cup/gsm2computer-nft.sh" revert
  if sudo ip netns exec "$NS" nft list table inet gsm2computer >/dev/null 2>&1; then
    echo "table still present after revert"
    exit 1
  fi
  sudo ip netns del "$NS"
  echo "=== rollback timer fires ==="
  sudo ip netns add "$NS"
  sudo ip netns exec "$NS" env \
    GSM2COMPUTER_PUBLIC_IF=eth0 \
    GSM2COMPUTER_NFT_STATE="$STATE" \
    GSM2COMPUTER_NFT_PERSIST="$STATE/persist.nft" \
    GSM2COMPUTER_NFT_ALLOW_SLEEP=1 \
    GSM2COMPUTER_NFT_ROLLBACK_MODE=sleep \
    GSM2COMPUTER_NFT_ROLLBACK_S=2 \
    GSM2COMPUTER_NFT_SKIP_LINK_CHECK=1 \
    bash "$ROOT/deploy/cup/gsm2computer-nft.sh" apply >/dev/null
  sudo ip netns exec "$NS" nft list table inet gsm2computer >/dev/null
  sleep 3
  if sudo ip netns exec "$NS" nft list table inet gsm2computer >/dev/null 2>&1; then
    echo "rollback timer left the table loaded"
    exit 1
  fi
  echo "rollback timer removed the table"
  sudo ip netns del "$NS"
  echo "firewall ok"
} | tee "$OUT"
echo "wrote $OUT"
