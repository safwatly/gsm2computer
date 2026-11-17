#!/bin/bash
# Idempotent install of the gsm2computer Asterisk gateway on a Raspberry Pi
# (or any Debian arm64/amd64 box) that will hold the Quectel EC25-E.
#
#   sudo ./install.sh
#
# Re-running copies configs again, skips the chan_quectel build when the
# pinned commit is already installed, and does not rotate the AMI secret.
# Tailscale is not joined. See tailscale-join.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
CHAN_COMMIT="3d45c7f072131296a7e3c1a4faf5bb18751dbd87"
CHAN_SRC="/usr/local/src/asterisk-chan-quectel"
STAMP="/var/lib/gsm2computer/chan-quectel.commit"
STATE="/etc/gsm2computer"
SECRET_FILE="$STATE/ami.secret"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "install.sh needs root (it writes /etc/asterisk and builds a channel driver)" >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "install.sh expects Debian or Raspberry Pi OS (apt-get)" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  asterisk \
  asterisk-dev \
  asterisk-modules \
  build-essential \
  autoconf \
  automake \
  libtool \
  pkg-config \
  libasound2-dev \
  alsa-utils \
  git \
  python3 \
  ca-certificates

install -d -m 0755 "$STATE" /var/lib/gsm2computer /usr/local/sbin /etc/asterisk/gsm-gateway.d

if [[ ! -s "$SECRET_FILE" ]]; then
  umask 077
  openssl rand -hex 24 >"$SECRET_FILE"
  echo "AMI secret created at $SECRET_FILE"
  echo "Copy it to GSM2COMPUTER_ASTERISK_AMI_SECRET on cup. Do not commit it."
else
  echo "AMI secret unchanged ($SECRET_FILE)"
fi
chmod 600 "$SECRET_FILE"
SECRET="$(cat "$SECRET_FILE")"

so_installed() {
  find /usr -name 'chan_quectel.so' -print -quit 2>/dev/null
}

need_build=1
if [[ -f "$STAMP" ]] && [[ "$(cat "$STAMP")" == "$CHAN_COMMIT" ]] && [[ -n "$(so_installed || true)" ]]; then
  need_build=0
  echo "chan_quectel $CHAN_COMMIT already installed"
fi

if [[ "$need_build" -eq 1 ]]; then
  if [[ ! -d "$CHAN_SRC/.git" ]]; then
    rm -rf "$CHAN_SRC"
    git clone --filter=blob:none https://github.com/IchthysMaranatha/asterisk-chan-quectel.git "$CHAN_SRC"
  fi
  git -C "$CHAN_SRC" fetch --depth 1 origin "$CHAN_COMMIT"
  git -C "$CHAN_SRC" checkout --detach "$CHAN_COMMIT"
  ast_ver="$(asterisk -V | sed -n 's/.* \([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | head -n 1)"
  if [[ -z "$ast_ver" ]]; then
    echo "could not parse asterisk -V" >&2
    exit 1
  fi
  echo "building chan_quectel for Asterisk $ast_ver"
  (
    cd "$CHAN_SRC"
    ./bootstrap
    ./configure --with-astversion="$ast_ver"
    make
    make install
  )
  if [[ -z "$(so_installed || true)" ]]; then
    echo "chan_quectel.so was not installed under /usr" >&2
    exit 1
  fi
  printf '%s\n' "$CHAN_COMMIT" >"$STAMP"
fi

install -m 0644 "$ROOT/asterisk/quectel.conf" /etc/asterisk/quectel.conf
install -d -m 0755 /etc/asterisk/gsm-gateway.d
install -m 0644 "$ROOT/asterisk/extensions.conf" /etc/asterisk/gsm-gateway.d/extensions.conf
if ! grep -q 'gsm-gateway.d/extensions.conf' /etc/asterisk/extensions.conf; then
  printf '\n#include "gsm-gateway.d/extensions.conf"\n' >>/etc/asterisk/extensions.conf
fi

# Debian ships AMI disabled and bound to loopback. Turn it on without
# touching a bind address the operator already changed.
python3 - <<'PY'
from pathlib import Path
path = Path("/etc/asterisk/manager.conf")
text = path.read_text(encoding="utf-8") if path.exists() else "[general]\nenabled = no\nport = 5038\nbindaddr = 127.0.0.1\n"
lines = []
for line in text.splitlines():
    stripped = line.strip()
    if stripped == "enabled = no" or stripped == "enabled=no":
        lines.append("enabled = yes")
    elif stripped in ("bindaddr = 127.0.0.1", "bindaddr=127.0.0.1"):
        lines.append("bindaddr = 0.0.0.0")
    else:
        lines.append(line)
if not any(line.strip().startswith("enabled") for line in lines):
    lines.insert(1, "enabled = yes")
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

if ! grep -q 'manager.d/\*\.conf' /etc/asterisk/manager.conf && ! grep -q 'manager.d/*.conf' /etc/asterisk/manager.conf; then
  printf '\n#include "manager.d/*.conf"\n' >>/etc/asterisk/manager.conf
fi
install -d -m 0755 /etc/asterisk/manager.d
umask 027
cat > /etc/asterisk/manager.d/gsm-gateway.conf <<EOF
; gsm2computer-managed
[gsm-gateway]
secret = ${SECRET}
deny = 0.0.0.0/0.0.0.0
permit = 127.0.0.1/255.255.255.255
permit = 100.64.0.0/255.192.0.0
read = system,call,user,dtmf
write = system,call,originate,command
EOF
chmod 640 /etc/asterisk/manager.d/gsm-gateway.conf
chown root:asterisk /etc/asterisk/manager.d/gsm-gateway.conf 2>/dev/null || true

# Hub URL and AudioSocket target. Edit the host if MagicDNS differs.
if [[ ! -f "$STATE/gateway.env" ]]; then
  cat > "$STATE/gateway.env" <<'EOF'
GSM_GATEWAY_HUB_BASE=http://hub.mining-ling.ts.net:8787
GSM_GATEWAY_AUDIOSOCKET=hub.mining-ling.ts.net:9092
GSM_GATEWAY_DEVICE=ec25
GSM_GATEWAY_QAUDMOD=3
EOF
  echo "Wrote $STATE/gateway.env (edit the hub host if this tailnet differs)"
fi
chmod 644 "$STATE/gateway.env"
install -d -m 0755 /etc/systemd/system/asterisk.service.d
cat > /etc/systemd/system/asterisk.service.d/gsm-gateway.conf <<EOF
[Service]
EnvironmentFile=$STATE/gateway.env
EOF

for module in func_curl.so app_audiosocket.so chan_quectel.so; do
  if ! grep -q "load => $module" /etc/asterisk/modules.conf 2>/dev/null; then
    printf 'load => %s\n' "$module" >>/etc/asterisk/modules.conf
  fi
done

install -m 0755 "$ROOT/uac_at.py" /usr/local/sbin/gsm-gateway-enable-uac
install -m 0755 "$ROOT/gsm-gateway-sms" /usr/local/sbin/gsm-gateway-sms
install -m 0755 "$ROOT/tailscale-join.sh" /usr/local/sbin/gsm-gateway-tailscale
install -m 0644 "$ROOT/systemd/gsm-gateway-uac.service" /etc/systemd/system/gsm-gateway-uac.service

# Point quectel.conf at the sound card and AT port when they are already here.
python3 - <<'PY'
import subprocess
from pathlib import Path
import importlib.util
spec = importlib.util.spec_from_file_location("uac_at", "/usr/local/sbin/gsm-gateway-enable-uac")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
conf = Path("/etc/asterisk/quectel.conf")
text = conf.read_text(encoding="utf-8")
try:
    listing = subprocess.check_output(["aplay", "-l"], text=True, stderr=subprocess.DEVNULL)
except (OSError, subprocess.CalledProcessError):
    listing = ""
card = mod.alsa_card_id(listing)
port = None
try:
    port = mod.find_at_port()
except OSError:
    port = None
updated = mod.update_quectel_conf(text, data_port=port, alsa_id=card)
if updated != text:
    conf.write_text(updated, encoding="utf-8")
    print(f"quectel.conf data={port or 'unchanged'} alsadev={card or 'unchanged'}")
PY

systemctl daemon-reload
systemctl enable gsm-gateway-uac.service
systemctl enable asterisk.service
systemctl restart gsm-gateway-uac.service || true
if systemctl is-active --quiet asterisk; then
  asterisk -rx "module load chan_quectel.so" || true
  asterisk -rx "quectel reload now" || true
  asterisk -rx "dialplan reload" || true
  asterisk -rx "manager reload" || true
else
  systemctl restart asterisk
fi

echo
echo "Gateway packages and Asterisk config are in place."
echo "Tailscale is not joined. Run: /usr/local/sbin/gsm-gateway-tailscale"
echo "Then put the Pi's tailscale IP and $SECRET_FILE into the hub env"
echo "(see hub.env.example). Leave GSM2COMPUTER_ASTERISK_GATEWAY off until cutover."
