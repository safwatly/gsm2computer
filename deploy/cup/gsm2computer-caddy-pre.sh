#!/usr/bin/env bash
# Refuse to start Caddy unless GSM2COMPUTER_PUBLIC_BIND is one specific address.
# An empty value adapts to a server with no listener. 0.0.0.0 and :: are
# wildcards and would sit on top of Tailscale Serve's tailnet :443.
set -euo pipefail

bind=${GSM2COMPUTER_PUBLIC_BIND-}
python3 - "$bind" <<'PY'
import ipaddress
import sys

raw = sys.argv[1].strip()
if not raw:
    sys.exit(
        "gsm2computer-caddy: GSM2COMPUTER_PUBLIC_BIND is empty; "
        "Caddy would start with no listener"
    )
if any(ch in raw for ch in " \t,*"):
    sys.exit(f"gsm2computer-caddy: GSM2COMPUTER_PUBLIC_BIND must be one address, not {raw!r}")
try:
    address = ipaddress.ip_address(raw)
except ValueError:
    sys.exit(
        f"gsm2computer-caddy: GSM2COMPUTER_PUBLIC_BIND must be a specific IP, not {raw!r}"
    )
if address.is_unspecified:
    sys.exit(
        f"gsm2computer-caddy: GSM2COMPUTER_PUBLIC_BIND {address} is a wildcard; "
        "set the public address (cup: 62.83.35.197)"
    )
print(address)
PY
