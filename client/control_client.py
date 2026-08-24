#!/usr/bin/env python3
"""
Minimal control client for the ESP8266 TLS server.

Trust model mirrors SSH host-key checking: the ESP's certificate fingerprint
is read from device_fingerprint.txt (written by tools/gen_creds.sh). If the
device presents a different key -- wrong device, or something impersonating
it -- this refuses to continue instead of silently connecting.

Secrets (ESP_HOST, AUTH_TOKEN) come from .env -- copy .env.example to .env
and fill it in first.
"""
import socket
import ssl
import hashlib
import sys
from pathlib import Path

PORT = 8443
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def require(env: dict, key: str) -> str:
    value = env.get(key)
    if not value:
        print(f"Missing {key} in .env -- copy .env.example to .env and fill it in.")
        sys.exit(1)
    return value


def load_fingerprint() -> str:
    fp_file = PROJECT_ROOT / "device_fingerprint.txt"
    if not fp_file.exists():
        print(f"{fp_file} not found -- run tools/gen_creds.sh first.")
        sys.exit(1)
    # openssl's output line looks like: SHA256 Fingerprint=AA:BB:CC:...
    line = fp_file.read_text().strip()
    _, _, fp = line.partition("=")
    if not fp:
        print(f"Couldn't parse a fingerprint out of {fp_file}.")
        sys.exit(1)
    return fp.strip()


def fingerprint_matches(der_cert: bytes, expected: str) -> bool:
    fp = hashlib.sha256(der_cert).hexdigest().upper()
    fp_colon = ":".join(fp[i : i + 2] for i in range(0, len(fp), 2))
    return fp_colon == expected.upper()


def recv_line(sock, buffer: bytearray) -> str:
    """
    Reads a single '\\n'-terminated line from a stream socket, buffering
    across recv() calls so a message split across reads (or several
    messages arriving in one read) is handled correctly.
    """
    while b"\n" not in buffer:
        chunk = sock.recv(256)
        if not chunk:
            raise ConnectionError("Connection closed by server")
        buffer.extend(chunk)
    line, _, rest = buffer.partition(b"\n")
    del buffer[: len(line) + 1]
    return line.decode(errors="replace").strip()


def main():
    env = load_env(PROJECT_ROOT / ".env")
    host = require(env, "ESP_HOST")
    auth_token = require(env, "AUTH_TOKEN")
    expected_fingerprint = load_fingerprint()

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # manual pinning below replaces CA trust

    raw = socket.create_connection((host, PORT), timeout=5)
    tls = ctx.wrap_socket(raw, server_hostname=host)

    der_cert = tls.getpeercert(binary_form=True)
    if not fingerprint_matches(der_cert, expected_fingerprint):
        print("!! Certificate fingerprint mismatch -- refusing to continue.")
        tls.close()
        sys.exit(1)

    print("Device identity verified.")

    buf = bytearray()

    tls.sendall((auth_token + "\n").encode())
    print(recv_line(tls, buf))

    while True:
        cmd = input("> ").strip()
        if not cmd:
            continue
        tls.sendall((cmd + "\n").encode())
        print(recv_line(tls, buf))
        if cmd == "QUIT":
            break

    tls.close()


if __name__ == "__main__":
    main()
