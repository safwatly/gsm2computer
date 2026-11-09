# Phonetunnel features

The user-facing path is: the phone keeps a tunnel to cup, cup can run a
shell on the phone, cup can adb to the phone, the tunnel comes back
after a drop, and Tailscale is restarted if it dies. The bridge app is
not one of these features. The module must not touch it.

| Feature | Drive | Evidence |
|---|---|---|
| [Tunnel comes up](tunnel-up.md) | `drive-tunnel` | `tunnel-up.txt` |
| [Shell through the tunnel](phone-shell.md) | `drive-shell` | `phone-shell.txt` |
| [Adb through the tunnel](adb-forward.md) | `drive-adb` | `adb-forward.txt` |
| [Reconnect](reconnect.md) | `drive-reconnect` | `reconnect.txt` |
| Sshd supervised while the session stays up | `drive-supervise` | `supervise.txt` |
| Cup Match, key limits, timers | `drive-policy` | `policy.txt` |
| [Tailscale watchdog](tailscale-watchdog.md) | `drive-watchdog` | `watchdog.txt` |

Launch first. See [../SKILL.md](../SKILL.md).
