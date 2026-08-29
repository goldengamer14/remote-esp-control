# ESP8266 Remote Control

A wireless, authenticated control channel for an ESP8266MOD (ESP-12E) that
also hosts its own NAT-routed WiFi network. Built to replace USB-tethered
development with a TLS-secured command connection — no full SSH server
(infeasible on this chip's RAM/flash budget), but the same trust model: the
device's identity is cryptographically pinned and verified on every
connection, the way SSH host keys work.

## Why this exists

The ESP8266 doesn't have the RAM or flash headroom to run a real SSH server
alongside application logic. This project gets the property that actually
matters from SSH — **verified device identity, not just an encrypted pipe**
— using TLS with a self-signed EC certificate pinned by the client, plus a
shared-secret token check for command authorization. On top of that control
channel, the ESP also runs in dual AP+STA WiFi mode with NAT (via lwIP's
NAPT), so a device connected to its SoftAP gets real internet access relayed
through the ESP's uplink to another network — useful as a portable WiFi
bridge/repeater, controllable entirely over the air.

**Status:** both foundation steps are done and verified working — see
[Roadmap](#roadmap). Currently a functioning remote-controlled NAT bridge
with a persistent list of known WiFi networks; traffic-level features
(step 3) haven't been started.

## Architecture

```
                    SoftAP (NAT'd)                    STA uplink
┌─────────────┐   192.168.4.x, WPA2   ┌───────────┐   home/other WiFi   ┌──────────┐
│ Phone/laptop │ ────────────────────▶ │  ESP8266   │ ───────────────────▶│ Internet │
│  (any device)│                        │  (12E)     │                     └──────────┘
└─────────────┘                         │  AP + STA  │
                                          │  NAPT      │
┌─────────────┐  WiFi + TLS (pinned)     │  TLS server│
│   Laptop     │ ────────────────────▶ │  cmd loop  │
│ control_     │  reaches either the     │  EEPROM cfg│
│ client.py    │  AP IP or STA IP        └───────────┘
└─────────────┘
```

The TLS control server isn't bound to a specific interface, so it's
reachable from either the SoftAP side (`192.168.4.1`) or the STA side
(whatever IP the ESP gets from its uplink network) — useful since some
commands (`STOP`, `RESTART`) intentionally only affect the STA link, while
`RELOAD` resets both and will drop a SoftAP-side session.

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
- No extra PlatformIO build flags needed for NAT — confirmed the default
  board config already links a NAPT-capable lwIP library
  (`liblwip2-536-feat.a`)

## Setup

### 1. Configure secrets

```bash
cp .env.example .env
```

Edit `.env` with your home/uplink WiFi SSID+password (`WIFI_SSID`/
`WIFI_PASSWORD`), a name+password for the ESP's own SoftAP
(`SOFTAP_SSID`/`SOFTAP_PASSWORD`, 8+ characters), a random auth token
(`AUTH_TOKEN`, e.g. `openssl rand -hex 32`), and the ESP's IP once you know
it (`ESP_HOST`). `.env` is gitignored — never commit it.

### 2. Generate the device's identity + embed secrets

```bash
tools/gen_creds.sh
```

Runs **entirely on your machine**: generates an EC keypair and self-signed
certificate, saves the SHA-256 fingerprint to `device_fingerprint.txt`, and
calls `tools/make_header.py` to bake the key, cert, and all `.env` secrets
into `include/device_creds.h`. Re-run this any time you rotate the device's
identity or change any embedded credential.

### 3. Build and flash (USB, first time only)

```bash
pio run -t upload
```

If `esptool` fails to sync, see [Troubleshooting](#troubleshooting) — this
is almost always an upload-speed or bootloader-mode issue, not a code
problem. If this is the *first* time this firmware has used AP mode on this
particular chip, a rocky first boot (garbled serial output, a reset or two)
is a known quirk from stale RF calibration data — a full erase
(`pio run -t erase`) before reflashing usually prevents it.

### 4. Get the ESP's IP(s)

Watch the serial output:

```bash
pio device monitor
```

You'll see `STA IP: 192.168.x.x` (your uplink network) and
`AP IP: 192.168.4.1` (the ESP's own SoftAP), followed by NAT setup and
`TLS control server listening`. Put the STA IP in `.env` as `ESP_HOST` for
normal use — switch to `192.168.4.1` if you ever need to reach it while
`STOP`ped from the STA side or otherwise only reachable via the SoftAP.

### 5. Connect

```bash
python3 client/control_client.py
```

Verifies the device's pinned certificate fingerprint, authenticates with
your token, and drops you into a prompt. Run `HELP` for the full command
list.

## Command reference

| Command | Effect |
|---|---|
| `PING` | Replies `PONG` |
| `STATUS` | Replies with uptime in ms |
| `HELP` | Lists all commands |
| `NETADD [-f] <ssid> <password>` | Saves a network to the EEPROM-backed known-network list. Quote either argument if it contains spaces. By default, refuses to save a network that isn't currently visible in a scan; `-f` skips that check (for a network you'll only be in range of later) |
| `PASSWD <new password>` | Changes and persists the SoftAP password (8+ characters) |
| `LIST [-P]` | Lists saved networks with status (`--` / `in range` / `connected`); `-P` also shows stored passwords |
| `CONNECT <ssid>` | Connects to a saved network by name, if currently in range. Distinguishes `ERR not in list` from `ERR not in range` |
| `DELETE <ssid>` | Removes a saved network from the list |
| `UPDATE <ssid> <new password>` | Changes the stored password for a saved network (8+ characters) |
| `RELOAD` | Full reset of **both** STA and AP, then reconnects to the current network and rebuilds the SoftAP + NAT. Will drop a SoftAP-side session |
| `STOP` | Drops the STA link only. AP and the control channel stay up |
| `RESTART` | Reconnects STA to the first known network currently in range. Does **not** touch the AP |
| `QUIT` | Closes the connection |

`RELOAD`/`RESTART`/`CONNECT`/`NETADD` can send a progress line (prefixed
`# `) before their final result — `control_client.py` already handles this,
printing progress lines and treating the first non-`# ` line as the answer.

## Project layout

```
platformio.ini            PlatformIO build config (board, port, flash mode)
src/main.cpp               Firmware: AP+STA WiFi, NAT, TLS server, EEPROM
                            config, command loop
include/device_creds.h     Generated -- embedded cert/key/secrets (gitignored)
tools/gen_creds.sh          Generates the device's EC identity + fingerprint
tools/make_header.py        Builds device_creds.h from the keypair + .env
client/control_client.py    Laptop-side TLS client: cert pinning, multi-line
                            response handling, auto-reconnect after RELOAD
.env.example                 Template for secrets (copy to .env)
```

## Troubleshooting

- **`esptool` fails with a sync/connect error during upload** — try adding
  `upload_speed = 115200` to `platformio.ini`, make sure no other program
  has the port open, and as a last resort hold the board's FLASH/BOOT
  button while it connects.
- **Serial output looks truncated or repeats like it's looping** — almost
  always a watchdog reset from a busy-loop with no `delay()`/`yield()` in
  `loop()`, not a printing bug.
- **Garbled serial output + resets right after first using AP mode** —
  known ESP8266 quirk from stale RF/config flash sectors; a full
  `pio run -t erase` then reflash usually fixes it permanently.
- **Client gets `AUTH_FAIL` immediately** — the server expects the auth
  token as the very first line after the TLS handshake; anything else
  (e.g. a browser's `GET / HTTP/1.1`) fails this by design. Use
  `control_client.py`, not a browser.
- **`control_client.py` times out or resets mid-session** — check which
  `ESP_HOST` you're pointed at. `STOP`/`RESTART`/`RELOAD` change STA
  connectivity; a session addressed to the STA IP can drop even when the
  command behaved correctly, simply because that address went down. The AP
  IP (`192.168.4.1`) is unaffected by anything except `RELOAD`.
- **`NETADD`/`CONNECT` says a network isn't in range when it should be** —
  check you're not wrapping single-word SSIDs in unnecessary quotes in a
  way that breaks parsing on an older client/firmware pair; the current
  version handles quoted and unquoted arguments correctly either way.

## Security notes

- The auth token and all saved network passwords live in flash as plain
  text (compiled into the binary for the token; EEPROM for saved networks).
  Fine for a LAN-only threat model; don't treat the compiled `.bin`/`.elf`
  or a dumped EEPROM image as safe to share.
- Certificate pinning here is fixed-fingerprint (TOFU-style, checked on
  every connection), not CA-chain validation — intentional, mirrors SSH
  host keys rather than browser TLS.
- `device.key`, `device.crt`, `device_fingerprint.txt`,
  `include/device_creds.h`, and `.env` are all gitignored. Regenerate them
  locally; don't try to share them through the repo.
- `LIST -P` and `NETADD`'s stored passwords are sent in plaintext over the
  TLS connection to whoever is authenticated on the control channel --
  reasonable given the token gates entry, but worth knowing if you ever
  widen who holds that token.

## Roadmap

1. ~~**SoftAP hosting**~~ — done. ESP8266 hosts its own NAT-routed WiFi
   network (SoftAP), verified working end-to-end (raw IP routing, DNS
   resolution, real throughput), using the same TLS/cert-pinning trust
   model as the original control channel.
2. ~~**Richer terminal**~~ — done. Full command set (`NETADD`, `PASSWD`,
   `RELOAD`, `STOP`, `RESTART`, `LIST`, `CONNECT`, `DELETE`, `UPDATE`,
   `HELP`) with a persistent, roaming-capable list of known networks
   stored in EEPROM.
3. **Traffic-level features** — not started. Exploratory: lightweight
   firewalling, bandwidth shaping, or similar, on top of the working NAT
   bridge.

Each step is scoped and tackled on its own before moving to the next.
