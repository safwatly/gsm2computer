#!/usr/bin/env python3
"""Print the Android Signature{} id for an APK (dumpsys package shows the same hex).

Usage: python3 scripts/apk-cert-id.py gateway.apk
"""

import subprocess
import sys
import zipfile


def java_arrays_hash(data: bytes) -> str:
    """Arrays.hashCode(byte[]) then Integer.toHexString, matching Signature.toString()."""
    value = 1
    for byte in data:
        signed = byte if byte < 128 else byte - 256
        value = (31 * value + signed) & 0xFFFFFFFF
    return format(value, "x")


def _u32(data: bytes, offset: int) -> tuple[int, int]:
    return int.from_bytes(data[offset : offset + 4], "little"), offset + 4


def _u64(data: bytes, offset: int) -> tuple[int, int]:
    return int.from_bytes(data[offset : offset + 8], "little"), offset + 8


def certs_from_v1(apk: zipfile.ZipFile) -> list[bytes]:
    names = [
        name
        for name in apk.namelist()
        if name.startswith("META-INF/") and name.upper().endswith((".RSA", ".DSA", ".EC"))
    ]
    if not names:
        return []
    pkcs7 = apk.read(names[0])
    der = subprocess.check_output(
        ["openssl", "pkcs7", "-inform", "DER", "-print_certs", "-outform", "DER"],
        input=pkcs7,
    )
    certs = []
    marker = b"\x30\x82"
    start = 0
    while True:
        index = der.find(marker, start)
        if index < 0 or index + 4 > len(der):
            break
        length = int.from_bytes(der[index + 2 : index + 4], "big")
        end = index + 4 + length
        if end > len(der):
            break
        certs.append(der[index:end])
        start = end
    return certs


def certs_from_signing_block(blob: bytes) -> list[bytes]:
    """Pull X.509 certs out of an APK v2/v3 signing block."""
    magic = b"APK Sig Block 42"
    eocd = blob.rfind(b"PK\x05\x06")
    if eocd < 0:
        return []
    central = int.from_bytes(blob[eocd + 16 : eocd + 20], "little")
    if central < 32 or blob[central - 16 : central] != magic:
        return []
    block_size = int.from_bytes(blob[central - 24 : central - 16], "little")
    block = blob[central - block_size : central - 24]
    certs = []
    offset = 0
    v2 = 0x7109871A
    v3 = 0xF05368C0
    while offset + 12 <= len(block):
        pair_len, offset = _u64(block, offset)
        if pair_len < 4 or offset + pair_len - 4 > len(block):
            break
        scheme, offset = _u32(block, offset)
        value = block[offset : offset + pair_len - 4]
        offset += pair_len - 4
        if scheme not in (v2, v3):
            continue
        signers_len, cursor = _u32(value, 0)
        signers_end = cursor + signers_len
        while cursor + 4 <= signers_end:
            signer_len, cursor = _u32(value, cursor)
            signer = value[cursor : cursor + signer_len]
            cursor += signer_len
            signed_len, sc = _u32(signer, 0)
            signed = signer[sc : sc + signed_len]
            digests_len, dc = _u32(signed, 0)
            cc = dc + digests_len
            certs_len, cc = _u32(signed, cc)
            certs_end = cc + certs_len
            while cc + 4 <= certs_end:
                cert_len, cc = _u32(signed, cc)
                certs.append(signed[cc : cc + cert_len])
                cc += cert_len
    return certs


def cert_ders(apk_path: str) -> list[bytes]:
    with open(apk_path, "rb") as handle:
        blob = handle.read()
    with zipfile.ZipFile(apk_path) as apk:
        certs = certs_from_v1(apk)
    if not certs:
        certs = certs_from_signing_block(blob)
    if not certs:
        raise SystemExit(f"no signer certificate in {apk_path}")
    return certs


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apk-cert-id.py <apk>")
    seen = []
    for cert in cert_ders(sys.argv[1]):
        cert_id = java_arrays_hash(cert)
        if cert_id in seen:
            continue
        seen.append(cert_id)
        print(cert_id)


if __name__ == "__main__":
    main()
