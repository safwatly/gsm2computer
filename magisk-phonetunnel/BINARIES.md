# Phone tunnel binaries

The Magisk module ships one executable, `bin/dropbearmulti`. Wrappers
named `dropbear`, `dbclient`, and `dropbearkey` exec that file.
There is no separate OpenSSH binary on the phone. Cup uses the VPS's
own OpenSSH server.

## dropbearmulti

| | |
|---|---|
| Upstream | Dropbear 2025.89 |
| Source | https://matt.ucc.asn.au/dropbear/releases/dropbear-2025.89.tar.bz2 |
| Source SHA-256 | `0d1f7ca711cfc336dc8a85e672cab9cfd8223a02fe2da0a4a7aeb58c9e113634` |
| Local options | `magisk-phonetunnel/localoptions.h` (password auth compiled out, no motd) |
| Programs | `dropbear`, `dbclient`, `dropbearkey` (multi-call) |
| Cross compiler | https://musl.cc/aarch64-linux-musl-cross.tgz |
| Toolchain tarball SHA-256 | `c909817856d6ceda86aa510894fa3527eac7989f0ef6e87b5721c58737a06c38` |
| Compiler | aarch64-linux-musl-gcc (GCC) 11.2.1 20211120 |
| Configure | `--host=aarch64-linux-musl --enable-static --disable-zlib --disable-syslog --disable-lastlog --disable-utmp --disable-utmpx --disable-wtmp --disable-wtmpx --disable-pututline --disable-pam` |
| Link | static-pie musl (`-static`). `file` reports `static-pie linked`: no dynamic linker, not Android bionic. |
| Binary SHA-256 | `460ca3a2e0dc13834bbcd36f2fa9cd8d6bda434a7167bf2b40e7dc478cefaec1` |
| `file` | /agent/repos/gsm2computer/magisk-phonetunnel/bin/dropbearmulti: ELF 64-bit LSB pie executable, ARM aarch64, version 1 (SYSV), static-pie linked, stripped |

Rebuild with `./magisk-phonetunnel/build-binaries.sh`. The script
refuses a Dropbear tarball whose SHA-256 does not match the pin above.
The toolchain checksum is recorded after download so a later rebuild
can be compared; musl.cc does not publish a separate checksum file.

Why Dropbear: current dbclient implements `ExitOnForwardFailure`,
`ServerAliveInterval`, `StrictHostKeyChecking`, and `BatchMode`.
The server supports `-D <dir>` for `authorized_keys`, `-j`/`-k`
to disable forwarding, and a localhost bind. One static binary covers
the phone sshd, the tunnel client, and host-key generation.

Syslog is compiled out because Android has no syslog daemon. The
supervisor appends the process's stderr to
`/data/adb/phonetunnel/phonetunnel.log`.

## Key format

`dbclient` in this build cannot load an OpenSSH private key
(`-----BEGIN OPENSSH PRIVATE KEY-----`). The loader treats the cipher
name at the start of the openssh-key-v1 blob as a length and exits
with `String too long`. The cup setup script therefore generates the
phone identity with `dropbearkey` (Debian/Ubuntu package `dropbear-bin`).
The cup-to-phone shell key stays an OpenSSH key, because cup's OpenSSH
client reads it and the phone only stores the public line.

Do not replace `/data/adb/phonetunnel/id_ed25519` with a key from
`ssh-keygen`. The supervisor refuses that file and logs the reason.
