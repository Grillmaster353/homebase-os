# HomeBase OS: from a blank card to a working dashboard

This tutorial installs **HomeBase OS v1.0.0** on **Raspberry Pi OS Lite (64-bit)**. You prepare the Pi from another computer, then use a terminal. No desktop, browser, Home Assistant installation, or host Python setup is needed on the Pi.

HomeBase runs locally in Docker. The GitHub website supplies the code and instructions; it does not host your dashboard. Each installation has its own account, encrypted credentials and HTTPS certificates.

## 1. Gather the hardware

For a first installation, use a Raspberry Pi 4 or 5 with 4 GB RAM or more, a suitable power supply, cooling appropriate to the model, and a reliable 32 GB or larger microSD card or supported SSD. This is a practical starting recommendation, not a claim that every Pi/model has been hardware-tested. A Docker build can use more resources than the running dashboard. Older or low-memory models need separate validation.

You also need a card reader, a Mac/Windows/Linux computer, internet access for the first image and Docker builds, and a trusted home network. Ethernet is simplest for first boot; supported Wi-Fi also works. Keep the Pi and the devices you want to discover on a network where local communication and multicast are allowed.

**Writing an OS image erases the selected storage device. Back up the card and check the target carefully.** Do not select a disk containing files you want to keep. This guide does not erase or configure anything until you carry out the steps yourself.

## 2. Install Raspberry Pi OS Lite

1. Download **Raspberry Pi Imager** from the [official Raspberry Pi software page](https://www.raspberrypi.com/software/). Install and open it on your preparation computer.
2. Select your actual Raspberry Pi model under **Device**.
3. Under **Operating System**, choose **Raspberry Pi OS Lite (64-bit)**. It may appear under **Raspberry Pi OS (other)**. Choose Lite, without the desktop; do not choose 32-bit or an unsupported older image.
4. Under **Storage**, select the intended card/SSD. Check its capacity and identity before proceeding.
5. Open the Imager **Customisation** pages. Menu wording can differ between Imager versions; the required settings are listed below.

Raspberry Pi's [OS installation guide](https://www.raspberrypi.com/documentation/computers/getting-started.html) covers the current Imager screens. [Raspberry Pi OS documentation](https://www.raspberrypi.com/documentation/computers/os.html) explains the Lite and 64-bit variants.

## 3. Configure the Pi before writing

In Imager, configure:

- **Hostname:** for example `homebase-pi`. This becomes the Pi's SSH name, usually `homebase-pi.local`.
- **Locale:** your time zone and keyboard layout. Choose the country where the Pi will be used.
- **Linux username/password:** create your own account, for example `piadmin`, with a unique strong password. Do not assume the old `pi` / `raspberry` credentials exist. This Linux account is separate from the HomeBase dashboard account created later.
- **Wi-Fi:** if using it, enter the exact network name, password and wireless country. Use a band supported by your Pi. Ethernet users can connect a cable instead.
- **Remote Access / SSH:** enable SSH. Prefer an existing SSH public key if you already use keys; provide only the public `.pub` key. Otherwise select password authentication with the strong Linux password you chose. Never upload or paste a private SSH key into an image or website.

Raspberry Pi Connect is not needed for this local installation. You do not need router port forwarding or a public tunnel. Review the settings, write the image and let Imager finish verification before ejecting the card. See the official [SSH setup instructions](https://www.raspberrypi.com/documentation/computers/remote-access.html).

## 4. Boot and find the Pi

Insert the prepared storage, attach Ethernet if using it, then connect power. Give first boot time to complete its initial configuration. A steady power indicator alone does not prove networking is ready.

On your preparation computer, open Terminal (Mac/Linux) or PowerShell (Windows). Replace `piadmin` and the hostname with the values you chose:

```sh
ssh piadmin@homebase-pi.local
```

If `.local` does not resolve, find the Pi's address in your router's connected-device list or on a locally attached Pi console, then use:

```sh
ssh piadmin@192.168.1.20
```

The IP above is an example. Use your Pi's actual address. On first connection, SSH asks about a new host key. Verify that you are connecting to the intended Pi and compare its host-key fingerprint over a trusted local path before accepting. A Pi console can show its ED25519 host-key fingerprint with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`. Do not ignore a later unexpected changed-key warning.

Enter your Linux password when asked. Terminal does not display characters while you type it. A prompt on the Pi means the SSH connection succeeded.

## 5. Check the OS and apply updates

Run these on the **Pi**, not on your Mac or Windows computer:

```sh
uname -m
getconf LONG_BIT
cat /etc/os-release
hostname -I
```

Expected: `aarch64` or `arm64`, `64`, and Raspberry Pi OS based on a supported Debian release. The Docker instructions below target **Bookworm (12)** and **Trixie (13)**. `armv7l` or `32` means you installed a 32-bit OS: use a 64-bit image before continuing.

For a fresh Pi, review and install OS updates:

```sh
sudo apt update
sudo apt full-upgrade
sudo reboot
```

The reboot closes SSH. Reconnect with the same SSH command after the Pi returns. If package updates report an error, resolve it rather than continuing with a partially configured OS.

## 6. Install Docker Engine and Compose

Check whether Docker is already ready:

```sh
sudo docker version
sudo docker compose version
```

If both work, skip the installation commands. On an existing server with other containers or conflicting packages, follow Docker's migration guidance before changing packages. Do not remove Docker volumes or another application's data to resolve an installation conflict.

For a fresh supported 64-bit Raspberry Pi OS installation, the commands below add Docker's official Debian APT repository and install host packages. They are explicit system changes you choose to run. The HomeBase setup script does not run them for you.

```sh
sudo apt update
sudo apt install ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: $(. /etc/os-release && echo "$VERSION_CODENAME")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker compose version
sudo docker run --rm hello-world
```

A successful `hello-world` message verifies that Docker can start a container. The first run downloads a small official test image. The service starts at boot; this guide uses `sudo` rather than adding your user to the privileged Docker group.

Use [Docker's Debian installation instructions](https://docs.docker.com/engine/install/debian/) as the authoritative reference. Docker directs 64-bit Raspberry Pi OS users to that installation path. Other OS releases require checking their supported instructions before proceeding.

## 7. Download HomeBase OS

Still in the Pi SSH session:

```sh
cd ~
git clone https://github.com/Grillmaster353/homebase-os.git
cd homebase-os
ls
```

You should see `Dockerfile`, `compose.yaml`, `compose.lan.yaml`, `requirements.txt`, `requirements-tv.txt`, `app`, `web`, `scripts` and `docs`.

If you prefer a tagged source ZIP, download it from [GitHub Releases](https://github.com/Grillmaster353/homebase-os/releases), verify the matching SHA-256 checksum, extract it and enter its source folder. You can also copy an unpacked source folder over your existing authenticated SFTP connection. Do not copy someone else's runtime data, account or private certificates.

A source checkout includes everything needed to build HomeBase, but not Docker Engine, an AI model server, third-party account credentials or a prebuilt Pi SD-card image. Docker's first build downloads dependencies and may compile an ARM64 dependency; it can take considerably longer than later starts.

## 8. Run the interactive setup

From the source folder on the Pi:

```sh
sudo bash scripts/setup-pi.sh
```

The script checks Linux, 64-bit userspace, an interactive terminal and a running Docker Engine/Compose. It then asks for:

1. **The Pi's private IPv4 address.** Check `hostname -I` and your router's client list. Select the address reachable by your home devices, rather than a Docker bridge, VPN or public address. The proposed default comes from the Pi's routing table.
2. **A dashboard username.** The default suggestion is `admin`; choose your own if preferred.
3. **A dashboard password.** For a new account, enter and confirm a unique password of at least 14 characters at the hidden prompt. This is separate from your Pi's Linux password. No password is bundled in this release.

Setup builds the image, prepares the persistent account, creates a private local CA and server certificate, and starts the HTTPS service with automatic local discovery. Its runtime container is non-root, drops capabilities and has a read-only root filesystem. Docker's build stage installs dependencies inside the image, not into your Pi's host Python.

Do not interrupt an image build merely because compilation is quiet. Read any reported error. A successful run ends with **Dashboard ready: https://YOUR_PI_IP:8420**, a public CA file location and its hash. It may also print a unique `homebase-….local` dashboard name.

The SSH hostname from Imager and the generated dashboard hostname are different names. Use an HTTPS address actually printed by setup. Do not invent a `.local` address or assume `localhost` on your phone means the Pi.

## 9. Verify the running service

On the Pi, run:

```sh
sudo docker compose -f compose.yaml -f compose.lan.yaml ps
sudo docker compose -f compose.yaml -f compose.lan.yaml logs --tail=60
```

The `home-command` container should be running. Verify HTTPS using the generated local CA, substituting the Pi's real address:

```sh
curl --cacert homebase-rootCA.crt https://192.168.1.20:8420/health
```

Expected output includes `"status": "ok"` and version `1.0.0`. This verifies the server and certificate from the Pi; it does not establish another device's Wi-Fi reachability or every integration's compatibility. Do not use `curl -k` or bypass certificate validation to hide a certificate error.

If setup failed before “Dashboard ready”, inspect the logs and the troubleshooting section before treating the app as installed.

## 10. Trust your own HTTPS certificate on client devices

Setup places the **public** certificate `homebase-rootCA.crt` in the source folder. Transfer only this public certificate to your own client device over trusted SSH/SFTP. Keep `certs/server.key`, `.setup-ca/`, the Docker data volume and the vault private.

From a new terminal on your Mac/Linux computer, not inside the Pi SSH session:

```sh
scp piadmin@homebase-pi.local:~/homebase-os/homebase-rootCA.crt ~/Downloads/homebase-rootCA.crt
```

Replace the account, hostname and source path if you chose different ones. Windows users can use a trusted SFTP client or the `scp` available with their SSH tools and a suitable local destination.

Compare the transferred file's SHA-256 hash with the value setup printed:

```sh
# On the Pi
sha256sum homebase-rootCA.crt

# On a Mac, against the copied public file
shasum -a 256 ~/Downloads/homebase-rootCA.crt
```

- **Mac:** open Keychain Access, import your verified public CA certificate, then explicitly trust that certificate for SSL. This is a client trust change you perform yourself. [Apple's trust guide](https://support.apple.com/guide/keychain-access/change-the-trust-settings-of-a-certificate-kyca11871/mac) explains the controls.
- **iPhone / iPad:** install the verified certificate profile, then enable its root under **Settings → General → About → Certificate Trust Settings**. [Apple's certificate guide](https://support.apple.com/en-au/102390) covers manual trust. Transfer only through a trusted method; do not distribute the private key.
- **Other devices:** use the operating system's documented certificate-trust workflow for your own CA, or a certificate from an already trusted local setup. Do not install an unknown CA supplied by someone else's deployment.

Then visit the printed `https://PI_ADDRESS:8420` URL in your browser on the same network. A browser privacy warning means trust, hostname, date/time or IP configuration still needs fixing. Do not click through it as a substitute for correct setup.

## 11. Sign in and make the dashboard yours

Sign in using the dashboard account you created in setup. This is not your SSH account unless you deliberately chose the same username; keep their passwords separate.

- Open the top-left workspace menu, enter a display name and save. Reload once to confirm it persisted.
- Open **Settings → Security**. The app reports its account, encrypted vault, HTTPS, key permissions and action-approval controls. This is app-security information, not an assessment of your whole home.
- Enable two-factor authentication if wanted. Save the authenticator setup/backup securely before confirming a fresh six-digit code. There is no email recovery service; an offline password reset does not bypass a configured second factor.
- Open **Devices**. Automatic read-only discovery can find supported advertisements on the visible network. Cached neighbors can remain unverified; a large count does not prove those devices are online. Guest isolation, VLANs, sleeping devices and disabled discovery services can limit results.
- Live receive/send values are current traffic through the Pi's interface. They are not the maximum download speed of your internet plan.

## 12. Connect optional integrations

**TVs:** open **Smart TVs** in the sidebar for remotes and the digital keyboard. Choose a discovered TV or add its private address. New connections can use Direct mode without Home Assistant. Select the actual platform, start pairing only when ready, then enter the code shown on Android/Google TV or Apple TV. Samsung/LG use an on-screen authorization; Roku uses its enabled local control setting. Samsung/LG/Roku local channels require an explicit unencrypted-LAN opt-in. For Hisense, select its actual operating system. Compatible VIDAA firmware uses a separate MQTT/TLS adapter: add an authorised VIDAA credential profile and verified TLS trust before requesting its PIN. HomeBase includes no shared VIDAA client keys or passwords. A Chromecast connected by HDMI pairs as Android/Google TV separately from the TV’s built-in platform; select that HDMI input to see its code. Manual remote clicks require approval by default. In that TV’s settings, enable **Allow immediate remote buttons for this TV** and save to use instant buttons after pairing. Untick it to restore per-click approval. Assistant and automation proposals still require approval. VIDAA has a power-toggle button plus channel up/down; wake and channel behavior depend on the TV’s current mode and standby settings. **Remove TV** deletes the saved HomeBase connection; discovery can still see the physical TV. See [TV protocols and pairing](docs/TV.md).

**Cameras:** add an authorized Hikvision camera/NVR URL, username/password and the correct ISAPI channel. Discovery does not grant viewing rights or enable a camera API. This release provides JPEG snapshots, not a bundled RTSP recording/transcoding service.

**Servers:** add existing `node_exporter` endpoints for other machines. Host/container visibility determines the local metrics; remote CPU utilization needs two counter samples. Installing an exporter is a separate action on that machine.

**Home Assistant:** optional for its real entities, services, scenes and automations. Enter the authorized local URL/token. HomeBase does not install Home Assistant or configure it for you.

**AI:** choose an already running Ollama server and installed model, or a compatible provider. Actual response speed depends on hardware and model. A Pi is not guaranteed fast inference. A configured remote AI provider receives chat, selected telemetry and approved file contents. File/device proposals require approval; arbitrary shell execution is unavailable.

**ISP badge:** optional automatic mode checks `api.ipify.org` every 60 seconds for a public IPv4 change, then refreshes ISP details through `ipwho.is` on changes or metadata expiry. Both see the connection's public IP; credentials/inventory are not sent. A VPN may report its exit network. Manual names are also supported. See [integration requirements](docs/integrations.md).

## 13. Check the finished installation

You are ready to use HomeBase when all applicable checks succeed:

- Docker reports the container running and the CA-verified health check returns `ok`.
- A second device on your home network opens the HTTPS URL without a certificate warning and shows the sign-in screen.
- You can sign in, save a workspace name, reload it and see changing live telemetry.
- You can sign out; device telemetry requires authentication again.
- A compatible device can be paired using its physical permission/code flow, and a test command runs only after you approve it. Do this only when it is safe to operate that device.
- You know where the public certificate, private CA and persistent Docker volumes are stored and how to protect their backups.

A successful dashboard setup does not prove every TV/camera model works. Report target-hardware results separately from fixture tests. See [release validation](docs/VALIDATION.md).

## 14. Reboots, updates and private backups

Docker's restart policy brings the service back after a normal Pi reboot, provided Docker starts and the container was not deliberately stopped. Reconnect to the printed LAN URL after the Pi returns.

Run these in the source folder:

```sh
# Inspect or stop without deleting saved data
sudo docker compose -f compose.yaml -f compose.lan.yaml ps
sudo docker compose -f compose.yaml -f compose.lan.yaml stop

# Start an existing configured installation
sudo docker compose -f compose.yaml -f compose.lan.yaml up -d

# After reviewing and downloading updated source
git pull --ff-only
sudo docker compose -f compose.yaml -f compose.lan.yaml up -d --build
```

Before updates, make a private backup of both persistent volumes and `certs/` plus `.setup-ca/`. Keep the vault and its matching master key together. Backups contain credentials; never attach them to an issue or public release. Keep the same Compose project and volumes. **Do not use `docker compose down -v` as an update command:** it removes saved account/vault/workspace volumes.

If the Pi's LAN IP changes, rerun `sudo bash scripts/setup-pi.sh` with the new private address. Existing accounts and the local CA are retained, and the server certificate is refreshed. Protect the original CA; a changed CA requires new deliberate trust on clients. A router DHCP reservation can make the address more stable, but configure it yourself only if appropriate for your router.

See [operations, backups and account recovery](docs/operations.md) for maintenance details.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `homebase-pi.local` cannot be found | Confirm the hostname, same LAN and mDNS support. Find the Pi's current IP in the router client list and use it for SSH. |
| SSH is refused | Confirm SSH was enabled in Imager, boot completed and the address belongs to the Pi. Recheck from a Pi console if needed. |
| SSH password fails | Use the Linux username/password configured in Imager, not the HomeBase dashboard account. |
| Architecture check fails | Re-image with Raspberry Pi OS Lite 64-bit. A 64-bit kernel with 32-bit userspace is insufficient. |
| `docker` or Compose is missing | Complete the official Docker package installation and check `sudo docker compose version`. |
| Docker daemon cannot be reached | Check `sudo systemctl status docker` and `sudo docker info`. Read the service's error before retrying setup. |
| Build fails or the Pi runs out of memory | Save the error; check free storage with `df -h` and RAM with `free -h`. Low-memory models may not be suitable for the first build. Do not delete data volumes to free space blindly. |
| Script says it needs an interactive terminal | Run directly in your SSH terminal. Do not pipe the password or put it into a shell command/environment variable. |
| Browser says the connection is not private | Verify the copied public CA, client trust, system clock and certificate's address. If the Pi IP changed, rerun setup. Do not disable certificate verification. |
| Phone cannot reach the dashboard | Use the Pi's printed HTTPS URL, not phone `localhost`. Check same LAN/guest isolation, port 8420, container logs and existing firewall policy. No router port forwarding is needed. |
| TV or camera is missing | Check the actual supported platform, power state, API/discovery settings and multicast reachability. Try an authorized manual address. |
| Low Mbps while Speedtest is fast | The dashboard shows interface usage, not maximum internet bandwidth. |
| AI is slow/unreachable | Check the saved endpoint and model; use hardware capable of that model. Start with status-only mode to verify the app independently. |

## References and verification limits

The OS and Docker steps link to the official Raspberry Pi, Docker and Apple documentation above. Commands target a fresh supported 64-bit Pi OS installation. Source/approval/security behavior has fixture tests, while physical Pi image builds, network reachability and device compatibility require target verification. Review [VALIDATION.md](docs/VALIDATION.md) for the results actually recorded for this release. No default credentials, public dashboard or independent security-audit claim are included.
