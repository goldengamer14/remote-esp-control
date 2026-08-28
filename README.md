# ESP8266 Remote Control

A wireless, authenticated control channel for an ESP8266MOD (ESP-12E), built
to replace USB-tethered development with a TLS-secured command connection —
no full SSH server (infeasible on this chip's RAM/flash budget), but the
same trust model: the device's identity is cryptographically pinned and
verified on every connection, the way SSH host keys work.

## Why this exists

The ESP8266 doesn't have the RAM or flash headroom to run a real SSH server
alongside application logic. This project gets the property that actually
matters from SSH — **verified device identity, not just an encrypted pipe**
— using TLS with a self-signed EC certificate pinned by the client, plus a
shared-secret token check for command authorization. It's designed to stay
inside the ESP8266's constraints while still being a control channel you can
trust isn't being spoofed.

**Status:** minimal authenticated remote terminal (`PING` / `STATUS` /
`QUIT`), working over WiFi. This is a foundation step — see
[Roadmap](#roadmap).

## Architecture

```
┌─────────────┐   WiFi + TLS (pinned cert)   ┌──────────────────┐
│   Laptop     │ ───────────────────────────▶ │   ESP8266 (12E)   │
│ control_     │  1. TLS handshake             │  BearSSL TLS      │
│ client.py    │  2. verify server fingerprint  │  server            │
│              │  3. send AUTH_TOKEN            │  token check       │
│              │  4. send commands (PING, ...)  │  command loop      │
└─────────────┘ ◀─────────────────────────────  └──────────────────┘
```

The ESP's EC keypair is generated **offline** on your own machine (never on
the device, never over the network) and compiled into the firmware. The
laptop client pins the certificate's SHA-256 fingerprint and refuses to
proceed if a connection ever presents a different one.

## Prerequisites

- An ESP8266MOD (ESP-12E) module or dev board (tested on a NodeMCU/Wemos-style
  board with a built-in USB-serial chip)
- Linux (Debian/Arch tested) with the board enumerating as `/dev/ttyUSB0`
- [PlatformIO Core](https://platformio.org/) — install via the
  [PlatformIO IDE extension for VS Code](https://marketplace.visualstudio.com/items?itemName=platformio.platformio-ide),
  or `pip install platformio --break-system-packages`
- Python 3 (standard library only — no pip installs needed for the client
  or the credential-generation tooling)
- OpenSSL (for generating the device's identity keypair)

## Setup

### 1. Configure secrets

```bash
cp .env.example .env
```

Edit `.env` with your real WiFi SSID/password, a random auth token
(`openssl rand -hex 32`), and the ESP's IP address once you know it. `.env`
is gitignored — never commit it.

### 2. Generate the device's identity + embed secrets

```bash
tools/gen_creds.sh
```

This runs **entirely on your machine**: generates an EC keypair and
self-signed certificate, saves the SHA-256 fingerprint to
`device_fingerprint.txt`, and calls `tools/make_header.py` to bake the
key, cert, and your `.env` secrets into `include/device_creds.h`. Re-run
this any time you want to rotate the device's identity or change the
embedded WiFi/auth credentials.

### 3. Build and flash (USB, first time only)

```bash
pio run -t upload
```

If `esptool` fails to sync, see [Troubleshooting](#troubleshooting) — this
is almost always an upload-speed or bootloader-mode issue, not a code
problem.

### 4. Get the ESP's IP

Watch the serial output:

```bash
pio device monitor
```

You should see WiFi connect, then `IP: 192.168.x.x` and
`TLS control server listening`, printed once. Put that IP in `.env` as
`ESP_HOST`.

### 5. Connect

```bash
python3 client/control_client.py
```

This verifies the device's pinned certificate fingerprint, authenticates
with your token, and drops you into a prompt. Try `PING`, `STATUS`, `QUIT`.

## Project layout

```
platformio.ini          PlatformIO build config (board, port, flash mode)
src/main.cpp             Firmware: WiFi connect, TLS server, command loop
include/device_creds.h   Generated -- embedded cert/key/secrets (gitignored)
tools/gen_creds.sh        Generates the device's EC identity + fingerprint
tools/make_header.py      Builds device_creds.h from the keypair + .env
client/control_client.py  Laptop-side TLS client with cert pinning
.env.example               Template for secrets (copy to .env)
```

## Troubleshooting

- **`esptool` fails with a sync/connect error during upload** — try adding
  `upload_speed = 115200` to `platformio.ini`, make sure no other program
  (a serial monitor, etc.) has the port open, and as a last resort hold the
  board's FLASH/BOOT button while it connects.
- **Serial output looks truncated or repeats like it's looping** — this is
  almost always a watchdog reset from a busy-loop with no `delay()`/`yield()`
  in `loop()`, not a printing bug. Make sure the idle branch of `loop()`
  yields.
- **Client gets `AUTH_FAIL` immediately** — the server expects the auth
  token as the very first line after the TLS handshake; anything else
  (e.g. a browser's `GET / HTTP/1.1`) will fail this check by design. Use
  `control_client.py`, not a browser.
- **Responses seem to arrive one command late** — this was a client-side
  line-buffering bug in an earlier version of `control_client.py`, fixed by
  reading until a newline instead of assuming one `recv()` call equals one
  message. If you're on the current version this shouldn't recur.

## Security notes

- The auth token lives in flash as a plain string inside the compiled
  firmware binary. Fine for a LAN-only threat model; don't treat the
  compiled `.bin`/`.elf` as safe to share.
- Certificate pinning here is fixed-fingerprint (TOFU-style, checked on
  every connection), not CA-chain validation — this is intentional, and
  mirrors how SSH host keys work rather than how browser TLS works.
- `device.key`, `device.crt`, `device_fingerprint.txt`, `include/device_creds.h`,
  and `.env` are all gitignored. Regenerate them locally; don't try to share
  them through the repo.

## Roadmap

1. **SoftAP hosting** — ESP8266 hosts its own WiFi network (SoftAP) for a
   client device to connect to directly, using the same TLS/cert-pinning
   trust model, as an interim step before Ethernet bridging hardware is
   available.
2. **Richer terminal** — expand past `PING`/`STATUS`/`QUIT` into real
   config commands (`hostname`, `passwd`, `reload`, `restart`, `stop`, etc.).
3. **Traffic-level features** — exploratory: lightweight firewalling,
   bandwidth shaping, or similar, once the above is solid.

Each step is scoped and tackled on its own before moving to the next.



