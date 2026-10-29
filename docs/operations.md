# Operations

Run commands from the application folder on the Pi. Use the same Compose project and existing data volumes when updating.

## Status and logs

```sh
sudo docker compose -f compose.yaml -f compose.lan.yaml ps
sudo docker compose -f compose.yaml -f compose.lan.yaml logs --tail=60
```

The service is `home-command`, its port is `8420`, and the stable Compose project name is `home-command`. Runtime data and approved assistant files persist in the `command-data` and `command-workspace` named volumes belonging to that project. Certificates live in `certs/`; the setup-only CA private key lives in `.setup-ca/`.

## Stop, start and update

```sh
sudo docker compose -f compose.yaml -f compose.lan.yaml stop
sudo docker compose -f compose.yaml -f compose.lan.yaml up -d

# After updating source, rebuild without deleting volumes
sudo docker compose -f compose.yaml -f compose.lan.yaml up -d --build
```

Do not run `down -v` for an update: it deletes the administrator account, encrypted vault and workspace. Keep runtime directories and volumes outside any source upload.

## IP changes and certificates

Run `sudo bash scripts/setup-pi.sh` again and supply the Pi's current private IPv4. Setup preserves an existing account and CA, refreshes its server certificate, and verifies HTTPS against that CA. The generated `homebase-….local` hostname is persisted with the data volume. Discovery pauses after a network change; start it again from the dashboard when appropriate.

Copy only `homebase-rootCA.crt` to clients and follow the manual trust steps in [the terminal guide](../START-HERE.md). Do not bypass warnings. A certificate warning can indicate an untrusted CA, wrong address, expired certificate or an incorrect system clock.

## Password maintenance

Stop the service before using the offline account tool:

```sh
sudo docker compose -f compose.yaml -f compose.lan.yaml stop
sudo docker compose -f compose.yaml run --rm --no-deps --entrypoint python home-command \
  -m app.admin reset --username admin --data-dir /data
sudo docker compose -f compose.yaml -f compose.lan.yaml up -d
```

Use your actual account username. The new password is entered without echo. Existing second-factor configuration is preserved. There is no email account recovery or shipped password.

## Backups

Stop the service and use your existing secure Docker-volume backup process for both named volumes. Preserve `certs/` and `.setup-ca/` privately if you need to retain the same CA trust. The encrypted vault and its matching master key must stay together for recovery; someone who can read both can recover credentials. Store backups encrypted and restrict access. A source ZIP or Git repository intentionally contains none of this runtime data.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Setup refuses the OS | Use a 64-bit ARM64 or AMD64 Linux userland; 32-bit Raspberry Pi OS is unsupported by this profile. |
| Docker unavailable | Follow the official installation steps in the terminal guide and check `sudo systemctl status docker`. |
| HTTPS fails | Verify the selected IP belongs to the Pi, port 8420 is free, the generated CA is trusted and the certificate matches that IP or hostname. |
| `.local` name fails | The client and Pi need the same multicast-capable LAN. Try the certificate's direct private IP URL. VLANs and guest isolation can block discovery. |
| No camera/device found | Sleeping devices, VLANs, blocked multicast or disabled ONVIF can hide advertisements. Add an authorized integration manually. |
| Gateway latency unavailable | The image includes `ping`; the kernel must permit unprivileged ICMP and the gateway must reply. No capabilities or host policies are expanded automatically. |
| Provider name differs | Optional lookup identifies the current public exit organization. A VPN, proxy, wholesale network or hotspot may differ from the retail provider; set a manual label. |
| AI disconnected | Verify the saved provider URL, model and credentials. Ollama and its model must already be running. |
| TV command unsupported | Confirm the adapter's protocol, model support and completed pairing. A visible advertisement does not grant control. |

This release provides controlled fixture tests, not a claim that every Pi, television or camera has been tested.
