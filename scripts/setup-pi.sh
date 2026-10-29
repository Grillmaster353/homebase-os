#!/usr/bin/env bash
# Run explicitly on the target Pi. Never installs host dependencies or trust.
set -euo pipefail
umask 077

PROJECT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_DIR"

fail() { printf '\n%s\n' "$*" >&2; exit 1; }

[[ $(uname -s) == Linux ]] || fail 'Run this script on your Raspberry Pi / Linux server. See START-HERE.md.'
case $(uname -m) in
  aarch64|arm64|x86_64|amd64) ;;
  *) fail 'This bundle requires a 64-bit ARM64 or AMD64 OS. Install Raspberry Pi OS Lite (64-bit), then retry.' ;;
esac
[[ $(getconf LONG_BIT) == 64 ]] || fail 'The userland is 32-bit. Use Raspberry Pi OS Lite (64-bit).'
[[ $(id -u) == 0 ]] || fail 'Run: sudo ./scripts/setup-pi.sh (creates protected app files; does not install host packages).'
[[ -t 0 && -t 1 ]] || fail 'Run from an interactive terminal so the administrator password can be entered without echo.'
command -v docker >/dev/null 2>&1 || fail 'Docker is not installed. START-HERE.md has the official user-run installation steps.'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose v2 is required. See START-HERE.md.'
docker info >/dev/null 2>&1 || fail 'Docker is not running. Check: sudo systemctl status docker'
[[ "$PROJECT_DIR" != *:* ]] || fail 'Move this project to a folder whose path does not contain a colon.'
[[ ! -L certs && ! -L .setup-ca ]] || fail 'certs and .setup-ca must be ordinary project directories, not symbolic links.'

DEFAULT_IP=$(ip -4 route get 192.0.2.1 2>/dev/null | awk '{for(i=1;i<=NF;i++)if($i=="src"){print $(i+1);exit}}' || true)
printf '\nHomebase • Raspberry Pi setup\n'
printf 'This builds the image, prepares your local account and TLS, and starts HTTPS on your home LAN.\n'
printf 'No router forwarding, firewall changes, public tunnel, or certificate trust installation is performed.\n\n'
read -r -p "Pi home-network IPv4 address [${DEFAULT_IP:-enter its private IPv4}]: " LAN_IP
LAN_IP=${LAN_IP:-$DEFAULT_IP}
[[ "$LAN_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || fail 'Enter a private IPv4 address such as 192.168.1.20.'
IFS=. read -r OCTET_A OCTET_B OCTET_C OCTET_D <<< "$LAN_IP"
for OCTET in "$OCTET_A" "$OCTET_B" "$OCTET_C" "$OCTET_D"; do
  (( 10#$OCTET <= 255 )) || fail 'An IPv4 octet is outside 0–255.'
done
A=$((10#$OCTET_A)); B=$((10#$OCTET_B))
[[ $A == 10 || ( $A == 172 && $B -ge 16 && $B -le 31 ) || ( $A == 192 && $B == 168 ) ]] || fail 'Use the Pi’s private home-network address (10.x, 172.16–31.x, or 192.168.x).'
LAN_IP="$A.$B.$((10#$OCTET_C)).$((10#$OCTET_D))"
read -r -p 'Dashboard administrator username [admin]: ' ADMIN_USERNAME
ADMIN_USERNAME=${ADMIN_USERNAME:-admin}
[[ "$ADMIN_USERNAME" =~ ^[A-Za-z0-9_.-]{1,64}$ ]] || fail 'Use 1–64 letters, digits, dots, dashes or underscores for the username.'

printf '\nBuilding the application image. This fetches container packages; host packages are unchanged.\n'
docker compose -f compose.yaml build home-command

printf '\nPausing any existing dashboard while setup checks its account and TLS files.\n'
docker compose -f compose.yaml stop home-command
docker compose -f compose.yaml run --rm --no-deps --entrypoint python home-command \
  -m app.admin init --if-missing --username "$ADMIN_USERNAME" --data-dir /data

# Only these project directories are created/reowned. Private CA material is
# mounted into the one-off setup container, never the dashboard container.
install -d -m 0700 -o 10001 -g 10001 "$PROJECT_DIR/certs" "$PROJECT_DIR/.setup-ca"
printf '\nPreparing a private local certificate authority and a certificate for this Pi.\n'
docker compose -f compose.yaml run --rm --no-deps --entrypoint python \
  --volume "$PROJECT_DIR/certs:/certs" \
  --volume "$PROJECT_DIR/.setup-ca:/setup-ca" \
  home-command -m app.lan_setup --output-dir /certs --ca-dir /setup-ca \
  --ip "$LAN_IP" --data-dir /data

[[ -f certs/server.crt && -f certs/server.key && -f certs/homebase-rootCA.crt ]] || fail 'Certificate setup did not produce all required files. The LAN service was not started.'
install -m 0644 certs/homebase-rootCA.crt "$PROJECT_DIR/homebase-rootCA.crt"

printf '\nStarting the authenticated HTTPS dashboard on your home LAN.\n'
docker compose -f compose.yaml -f compose.lan.yaml up -d home-command

READY=0
for ATTEMPT in {1..20}; do
  if docker compose -f compose.yaml -f compose.lan.yaml exec -T home-command \
    python -c 'import ssl,sys,urllib.request; urllib.request.urlopen("https://"+sys.argv[1]+":8420/health", context=ssl.create_default_context(cafile="/certs/homebase-rootCA.crt"), timeout=1).read()' \
    "$LAN_IP" >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done

printf '\nPublic CA certificate: %s/homebase-rootCA.crt\n' "$PROJECT_DIR"
printf 'CA file SHA-256: '
sha256sum homebase-rootCA.crt
if [[ $READY == 1 ]]; then
  printf '\nDashboard ready: https://%s:8420\n' "$LAN_IP"
else
  printf '\nThe container started, but HTTPS readiness could not be verified. Check:\n'
  printf '  sudo docker compose -f compose.yaml -f compose.lan.yaml logs --tail=60\n'
  exit 1
fi
printf '\nInstall and trust ONLY homebase-rootCA.crt on your own devices before opening the dashboard.\n'
printf 'Do not bypass certificate warnings. START-HERE.md explains Mac, iPhone and iPad trust.\n'
printf 'Automatic local discovery is enabled; camera credentials and API support are still required.\n'
printf 'Keep certs/, .setup-ca/ and Docker data volumes private. Never share private keys.\n'
