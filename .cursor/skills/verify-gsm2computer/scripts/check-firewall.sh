#!/usr/bin/env bash
# Render, syntax-check, apply, and revert the public TCP 443 drop-in inside a netns.
# The netns starts from the cup-shaped inet filter (22, UDP 41641, UDP 41642).
# Does not install rules on the host. Needs root (sudo).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../../../.." && pwd)
EVIDENCE=${GSM2COMPUTER_VERIFY_EVIDENCE:-/opt/cursor/artifacts/verify-gsm2computer}
mkdir -p "$EVIDENCE"
OUT=$EVIDENCE/firewall.txt
NS=gsm2nft-verify
STATE=$(mktemp -d /tmp/gsm-nft-XXXX)
chmod 777 "$STATE"
EXAMPLE=$ROOT/deploy/cup/nftables.filter.example.conf
# The example documents the include markers. The pre-apply host file in this
# test is that filter without the drop-in block, which is what cup has today.
python3 - "$EXAMPLE" "$STATE/nftables.conf" <<'PY'
import pathlib, sys
src, dest = sys.argv[1:]
text = pathlib.Path(src).read_text()
begin = "# BEGIN gsm2computer-public-entrance"
end = "# END gsm2computer-public-entrance"
start = text.find(begin)
stop = text.find(end)
if start != -1 and stop != -1:
    stop += len(end)
    if stop < len(text) and text[stop] == "\n":
        stop += 1
    text = text[:start] + text[stop:]
pathlib.Path(dest).write_text(text)
PY
cleanup_ns() { sudo ip netns del "$NS" 2>/dev/null || true; }
load_filter() {
  sudo ip netns exec "$NS" nft -f "$STATE/nftables.conf"
}
filter_dump() {
  sudo ip netns exec "$NS" nft list table inet filter
}
common_env=(
  GSM2COMPUTER_PUBLIC_IF=eth0
  GSM2COMPUTER_NFT_STATE="$STATE"
  GSM2COMPUTER_NFT_CONF="$STATE/nftables.conf"
  GSM2COMPUTER_NFT_SKIP_LINK_CHECK=1
)
{
  trap cleanup_ns EXIT
  echo "=== nft -c (not applied) ==="
  sudo GSM2COMPUTER_PUBLIC_IF=eth0 "$ROOT/deploy/cup/gsm2computer-nft.sh" check
  sudo ip netns del "$NS" 2>/dev/null || true
  sudo ip netns add "$NS"
  load_filter
  sudo ip netns exec "$NS" env \
    "${common_env[@]}" \
    GSM2COMPUTER_NFT_PERSIST="$STATE/persist.nft" \
    GSM2COMPUTER_NFT_ALLOW_SLEEP=1 \
    GSM2COMPUTER_NFT_ROLLBACK_MODE=sleep \
    GSM2COMPUTER_NFT_ROLLBACK_S=30 \
    bash "$ROOT/deploy/cup/gsm2computer-nft.sh" apply
  echo "=== loaded filter ==="
  table=$(sudo ip netns exec "$NS" nft list table inet filter)
  printf '%s\n' "$table"
  grep -q 'tcp dport 443 accept' <<<"$table"
  grep -q 'gsm2computer-public-tls' <<<"$table"
  grep -q 'udp dport 41642 accept' <<<"$table"
  grep -q 'udp dport 41641 accept' <<<"$table"
  grep -q 'tcp dport 22 accept' <<<"$table"
  grep -q 'rustdesk-and-hub-tailscale' <<<"$table"
  if sudo ip netns exec "$NS" nft list table inet gsm2computer >/dev/null 2>&1; then
    echo "netns gained inet gsm2computer; refusing"
    exit 1
  fi
  if sudo nft list ruleset 2>/dev/null | grep -q 'gsm2computer-public-tls'; then
    echo "host namespace gained the public TLS rule; refusing"
    exit 1
  fi
  echo "=== revert ==="
  sudo ip netns exec "$NS" env \
    "${common_env[@]}" \
    GSM2COMPUTER_NFT_PERSIST="$STATE/persist.nft" \
    bash "$ROOT/deploy/cup/gsm2computer-nft.sh" revert
  after=$(sudo ip netns exec "$NS" nft list table inet filter)
  if grep -q 'gsm2computer-public-tls' <<<"$after"; then
    echo "443 drop-in still present after revert"
    exit 1
  fi
  grep -q 'udp dport 41642 accept' <<<"$after"
  sudo ip netns del "$NS"
  echo "=== rollback timer fires ==="
  sudo ip netns add "$NS"
  load_filter
  sudo ip netns exec "$NS" env \
    "${common_env[@]}" \
    GSM2COMPUTER_NFT_PERSIST="$STATE/persist.nft" \
    GSM2COMPUTER_NFT_ALLOW_SLEEP=1 \
    GSM2COMPUTER_NFT_ROLLBACK_MODE=sleep \
    GSM2COMPUTER_NFT_ROLLBACK_S=2 \
    bash "$ROOT/deploy/cup/gsm2computer-nft.sh" apply >/dev/null
  armed=$(filter_dump)
  grep -q 'gsm2computer-public-tls' <<<"$armed"
  sleep 3
  rolled=$(filter_dump)
  if grep -q 'gsm2computer-public-tls' <<<"$rolled"; then
    echo "rollback timer left the 443 rule loaded"
    exit 1
  fi
  grep -q 'udp dport 41642 accept' <<<"$rolled"
  echo "rollback timer removed only the 443 rule"
  sudo ip netns del "$NS"
  if grep -q 'nft delete' "$ROOT/deploy/cup/gsm2computer-nft.service"; then
    echo "firewall unit still deletes rules on stop"
    exit 1
  fi
  echo "=== confirm failure keeps the rollback ==="
  sudo ip netns add "$NS"
  load_filter
  cat >"$STATE/systemctl" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >> "${GSM2COMPUTER_NFT_SYSTEMCTL_LOG:?}"
if [[ "$1" == "enable" && "${GSM2COMPUTER_NFT_SYSTEMCTL_FAIL:-}" == 1 ]]; then
  exit 1
fi
exit 0
EOF
  chmod 755 "$STATE/systemctl"
  : >"$STATE/systemctl.log"
  sudo ip netns exec "$NS" env \
    "${common_env[@]}" \
    GSM2COMPUTER_NFT_PERSIST="$STATE/persist.nft" \
    GSM2COMPUTER_NFT_ALLOW_SLEEP=1 \
    GSM2COMPUTER_NFT_ROLLBACK_MODE=sleep \
    GSM2COMPUTER_NFT_ROLLBACK_S=30 \
    bash "$ROOT/deploy/cup/gsm2computer-nft.sh" apply >/dev/null
  if sudo ip netns exec "$NS" env \
    "${common_env[@]}" \
    GSM2COMPUTER_NFT_PERSIST="$STATE/persist.nft" \
    GSM2COMPUTER_NFT_SBIN="$STATE/sbin" \
    GSM2COMPUTER_NFT_UNIT_DEST="$STATE/gsm2computer-nft.service" \
    GSM2COMPUTER_NFT_SYSTEMCTL="$STATE/systemctl" \
    GSM2COMPUTER_NFT_SYSTEMCTL_LOG="$STATE/systemctl.log" \
    GSM2COMPUTER_NFT_SYSTEMCTL_FAIL=1 \
    bash "$ROOT/deploy/cup/gsm2computer-nft.sh" confirm
  then
    echo "confirm succeeded while systemctl enable was set to fail"
    exit 1
  fi
  if [[ ! -f "$STATE/rollback-armed" || ! -f "$STATE/baseline.mode" ]]; then
    echo "failed confirm disarmed rollback or deleted the baseline"
    exit 1
  fi
  if grep -q 'gsm2computer-public.nft' "$STATE/nftables.conf"; then
    echo "failed confirm left the include in nftables.conf"
    exit 1
  fi
  failed=$(filter_dump)
  grep -q 'gsm2computer-public-tls' <<<"$failed"
  grep -q 'udp dport 41642 accept' <<<"$failed"
  echo "failed confirm left the timer and the 443 rule"
  sudo ip netns exec "$NS" env \
    "${common_env[@]}" \
    GSM2COMPUTER_NFT_PERSIST="$STATE/persist.nft" \
    bash "$ROOT/deploy/cup/gsm2computer-nft.sh" revert >/dev/null
  sudo ip netns del "$NS"
  echo "=== confirm persists before disarming ==="
  sudo ip netns add "$NS"
  load_filter
  : >"$STATE/systemctl.log"
  sudo ip netns exec "$NS" env \
    "${common_env[@]}" \
    GSM2COMPUTER_NFT_PERSIST="$STATE/persist-ok.nft" \
    GSM2COMPUTER_NFT_ALLOW_SLEEP=1 \
    GSM2COMPUTER_NFT_ROLLBACK_MODE=sleep \
    GSM2COMPUTER_NFT_ROLLBACK_S=30 \
    bash "$ROOT/deploy/cup/gsm2computer-nft.sh" apply >/dev/null
  sudo ip netns exec "$NS" env \
    "${common_env[@]}" \
    GSM2COMPUTER_NFT_PERSIST="$STATE/persist-ok.nft" \
    GSM2COMPUTER_NFT_SBIN="$STATE/sbin" \
    GSM2COMPUTER_NFT_UNIT_DEST="$STATE/gsm2computer-nft.service" \
    GSM2COMPUTER_NFT_SYSTEMCTL="$STATE/systemctl" \
    GSM2COMPUTER_NFT_SYSTEMCTL_LOG="$STATE/systemctl.log" \
    bash "$ROOT/deploy/cup/gsm2computer-nft.sh" confirm >/dev/null
  if [[ -f "$STATE/rollback-armed" || ! -f "$STATE/persist-ok.nft" ]]; then
    echo "successful confirm did not persist before cancelling rollback"
    exit 1
  fi
  grep -q 'gsm2computer-public-tls' "$STATE/persist-ok.nft"
  grep -q 'tcp dport 443' "$STATE/persist-ok.nft"
  if grep -q 'flush ruleset' "$STATE/persist-ok.nft"; then
    echo "persisted drop-in contains flush ruleset"
    exit 1
  fi
  grep -q 'include "'"$STATE/persist-ok.nft"'"' "$STATE/nftables.conf"
  confirmed=$(filter_dump)
  grep -q 'gsm2computer-public-tls' <<<"$confirmed"
  grep -q 'udp dport 41642 accept' <<<"$confirmed"
  echo "confirm kept 41642 and cancelled rollback only after the include was in place"
  : >"$STATE/systemctl.log"
  sudo ip netns exec "$NS" env \
    "${common_env[@]}" \
    GSM2COMPUTER_NFT_PERSIST="$STATE/persist-ok.nft" \
    GSM2COMPUTER_NFT_ROLLBACK_MODE=sleep \
    GSM2COMPUTER_NFT_UNIT_DEST="$STATE/gsm2computer-nft.service" \
    GSM2COMPUTER_NFT_SYSTEMCTL="$STATE/systemctl" \
    GSM2COMPUTER_NFT_SYSTEMCTL_LOG="$STATE/systemctl.log" \
    bash "$ROOT/deploy/cup/gsm2computer-nft.sh" revert >/dev/null
  reverted=$(filter_dump)
  if grep -q 'gsm2computer-public-tls' <<<"$reverted"; then
    echo "revert left the 443 rule loaded"
    exit 1
  fi
  grep -q 'udp dport 41642 accept' <<<"$reverted"
  if [[ -f "$STATE/persist-ok.nft" ]]; then
    echo "revert left the persisted drop-in in place"
    exit 1
  fi
  if grep -q 'BEGIN gsm2computer-public-entrance' "$STATE/nftables.conf"; then
    echo "revert left the include block in nftables.conf"
    exit 1
  fi
  grep -q 'disable --now gsm2computer-nft.service' "$STATE/systemctl.log"
  if [[ -e /etc/systemd/system/gsm2computer-nft.service ]]; then
    echo "host gained /etc/systemd/system/gsm2computer-nft.service"
    exit 1
  fi
  if sudo nft list table inet gsm2computer >/dev/null 2>&1; then
    echo "host namespace gained inet gsm2computer during revert"
    exit 1
  fi
  echo "revert used the override systemctl and left the host firewall alone"
  sudo ip netns del "$NS"
  echo "firewall ok"
} | tee "$OUT"
echo "wrote $OUT"
