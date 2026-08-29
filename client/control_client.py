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
import time
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


def read_response(tls, buf: bytearray) -> str:
    """
    A command can produce one or more lines: zero or more progress lines
    prefixed '# ' (printed as they arrive), followed by exactly one real
    result line, which is returned. This is what keeps multi-line replies
    (RELOAD, RESTART) from desyncing the one-command-one-response loop.
    """
    while True:
        line = recv_line(tls, buf)
        if line.startswith("# "):
            print(line)
            continue
        return line


def connect_and_verify(host: str, auth_token: str, expected_fingerprint: str):
    """One full connect + TLS handshake + fingerprint check + auth cycle."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # manual pinning below replaces CA trust

    raw = socket.create_connection((host, PORT), timeout=5)
    tls = ctx.wrap_socket(raw, server_hostname=host)

    der_cert = tls.getpeercert(binary_form=True)
    if not fingerprint_matches(der_cert, expected_fingerprint):
        tls.close()
        raise RuntimeError("Certificate fingerprint mismatch -- refusing to continue")

    tls.settimeout(20)  # RELOAD/RESTART can take up to 15s server-side
    buf = bytearray()

    tls.sendall((auth_token + "\n").encode())
    auth_resp = recv_line(tls, buf)
    if auth_resp != "AUTH_OK":
        tls.close()
        raise RuntimeError(f"Auth failed: {auth_resp}")

    return tls, buf


def wait_for_reconnect(host: str, auth_token: str, expected_fingerprint: str, max_wait: float = 60):
    """
    RELOAD tears down and rebuilds both STA and AP, which kills whatever
    TCP session sent the command if it arrived over the AP. Poll for a
    fresh connection to succeed rather than treating that as a fatal error.
    """
    print("Connection dropped (expected for RELOAD) -- waiting for AP to come back", end="", flush=True)
    start = time.monotonic()
    while time.monotonic() - start < max_wait:
        try:
            tls, buf = connect_and_verify(host, auth_token, expected_fingerprint)
            print("\nReconnected.")
            return tls, buf
        except (OSError, ssl.SSLError, RuntimeError):
            print(".", end="", flush=True)
            time.sleep(2)
    raise RuntimeError("Timed out waiting for the AP to come back after RELOAD")


def main():
    env = load_env(PROJECT_ROOT / ".env")
    host = require(env, "ESP_HOST")
    auth_token = require(env, "AUTH_TOKEN")
    expected_fingerprint = load_fingerprint()

    tls, buf = connect_and_verify(host, auth_token, expected_fingerprint)
    print("Device identity verified.")
    print("AUTH_OK")

    while True:
        cmd = input("> ").strip()
        if not cmd:
            continue

        try:
            tls.sendall((cmd + "\n").encode())
            print(read_response(tls, buf))
        except (OSError, ssl.SSLError) as e:
            if cmd == "RELOAD":
                tls, buf = wait_for_reconnect(host, auth_token, expected_fingerprint)
            else:
                print(f"!! Connection lost: {e}")
                tls.close()
                sys.exit(1)

        if cmd == "QUIT":
            break

    tls.close()


if __name__ == "__main__":
    main()

