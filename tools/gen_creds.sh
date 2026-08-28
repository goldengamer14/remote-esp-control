#!/usr/bin/env bash
# Run this on YOUR machine (not in any shared/sandboxed environment) so the
# private key never leaves it. Re-run any time you want to rotate identity.
set -euo pipefail
cd "$(dirname "$0")/.."

openssl ecparam -name prime256v1 -genkey -noout -out device.key
openssl req -new -x509 -key device.key -out device.crt -days 3650 \
  -subj "/CN=esp8266-control"

python3 tools/make_header.py device.key device.crt include/device_creds.h

echo
echo "Fingerprint (also saved to device_fingerprint.txt):"
openssl x509 -in device.crt -noout -fingerprint -sha256 | tee device_fingerprint.txt

