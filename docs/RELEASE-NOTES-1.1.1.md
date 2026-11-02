# HomeBase OS v1.1.1 — Classic Design Restored

This patch restores the familiar HomeBase design from before the liquid-glass update in both light and dark mode. The blue accent, saved theme choice, full-screen sign-in and rounded Devices filter rail remain.

## What changed

- Removed the liquid-glass backdrop, reflective panes and floating navigation overrides.
- Restored the earlier dashboard, forms, dialogs and remote presentation.
- Updated the public landing page and guides to describe the restored design.

This is a presentation change. Existing features, local accounts, encrypted settings, device pairings and action-approval settings retain their behavior. There is no data migration or new cloud dependency. The earlier v1.1.0 release remains available in the project history.

## New installation

Use **Raspberry Pi OS Lite (64-bit)** with Docker Engine and Compose v2. Follow the [complete installation tutorial](https://grillmaster353.github.io/homebase-os/setup.html) from preparing the OS card through SSH, Docker, HTTPS trust and first sign-in.

Download `homebase-os-v1.1.1-source.zip` and its matching `.sha256` file from this release. Verify the exact archive before extracting:

```sh
# macOS
shasum -a 256 -c homebase-os-v1.1.1-source.zip.sha256

# Linux
sha256sum -c homebase-os-v1.1.1-source.zip.sha256
```

In the extracted source folder on the Pi:

```sh
sudo bash scripts/setup-pi.sh
```

Setup asks you to choose your own administrator account. The source includes no default password, runtime vault, private certificate or AI model.

## Upgrade while keeping your setup

1. Back up your persistent Docker data/workspace volumes, `certs/` and `.setup-ca/` privately.
2. Keep the same installation folder, Compose project and persistent volumes. Review any local source edits first.
3. For a clean Git checkout, select the release and rebuild:

```sh
git status --short
git fetch origin tag v1.1.1
git checkout v1.1.1
sudo docker compose -f compose.yaml -f compose.lan.yaml up -d --build
```

Do **not** run `docker compose down -v`: it removes saved account, vault and workspace data. Existing accounts and settings are retained. Refresh your browser after the update; sign in again if the service restart expires your session. See [operations and backups](https://github.com/Grillmaster353/homebase-os/blob/v1.1.1/docs/operations.md).

## Validation and limits

Review the release commit's GitHub Actions checks and [validation history](https://github.com/Grillmaster353/homebase-os/blob/v1.1.1/docs/VALIDATION.md). Fixture tests and ARM64/AMD64 image builds do not establish physical Raspberry Pi performance or universal device compatibility.

This patch does not fix Samsung pairing prompts or change device protocols. Model, firmware, permissions and authorized pairing still determine support. Cameras use supported authenticated JPEG snapshots; no RTSP recorder or transcoder is included. Home Assistant and AI providers require their own configuration. The project has not undergone an independent security audit.

GitHub Pages hosts the guides. The authenticated dashboard runs on your own hardware and trusted home network.
