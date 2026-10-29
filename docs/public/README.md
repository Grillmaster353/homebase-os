# HomeBase OS

**v1.0.0 · A private home command centre for Raspberry Pi OS Lite 64-bit.**

HomeBase OS brings network visibility, authorized smart-device controls, camera snapshots, server monitoring and an approval-based AI assistant into one responsive dark dashboard. It runs on your own hardware and works from a Mac, iPhone or iPad on the same trusted home network.

This public distribution uses **Docker on a 64-bit Linux host**. Raspberry Pi OS Lite needs no desktop environment or browser on the Pi. The application does not install host packages or publish itself to the internet.

## Quick start

With Docker Engine and Compose v2 already installed:

```sh
git clone https://github.com/Grillmaster353/homebase-os.git homebase-os
cd homebase-os
sudo bash scripts/setup-pi.sh
```

Alternatively, download the source ZIP and open the extracted folder. Follow [START-HERE.md](START-HERE.md) for the complete tutorial, starting with Raspberry Pi OS Lite installation, SSH, Docker, HTTPS trust and first sign-in. You can also read the [step-by-step website guide](https://grillmaster353.github.io/homebase-os/setup.html).

Setup asks for the Pi's private IPv4 address and an administrator username (default `admin`). It prompts for a password without echo, creates local TLS material, and starts the authenticated HTTPS service only after account setup succeeds. No account or password is shipped with the source. Existing accounts and encrypted settings survive reruns.

## What works

| Area | Supported behavior |
| --- | --- |
| Dashboard | Responsive dark interface, live charts, searchable inventory, activity history and approval queue. |
| Discovery | Local neighbor, SSDP and supported ONVIF advertisements; automatic refresh after explicitly enabled or started with the Pi setup profile. Network changes pause discovery. |
| Network | Real interface throughput, host telemetry, and gateway latency. These describe current usage, not your ISP plan maximum. |
| Provider/router badge | Any manual provider/router name; opt-in automatic identification with 60-second public-IP change checks; local manufacturer-prefix matching and authentic bundled logos with a generic fallback. |
| Smart TVs | Direct Samsung, Android/Google TV, Apple TV, Roku and LG webOS adapters with authorized pairing and command approval. Hisense follows its actual Android/Roku platform; VIDAA-only models are not claimed. Home Assistant is optional. See [TV protocols and limits](docs/TV.md). |
| Cameras | Authorized Hikvision ISAPI JPEG snapshots; supported local camera advertisements. This release does not bundle an RTSP video transcoder. |
| Servers | Host CPU/RAM/disk/network metrics and configured Prometheus `node_exporter` endpoints. |
| Smart home | Read Home Assistant entities and propose supported service calls, scenes and automations. |
| AI | Ollama or an explicitly configured compatible provider; bounded conversation context and proposed file/device actions. Each action requires approval. No arbitrary shell execution. |

Addresses and credentials are supplied by the person deploying the application. Device discovery does not bypass authentication, enable a camera API or pair a TV automatically. See [integration requirements and limits](docs/integrations.md).

## Local-first security

Passwords use salted scrypt hashes. Sessions use protected cookies, CSRF validation, timeouts and rate limits; optional authenticator-app TOTP adds a second factor. Integration credentials use AES-256-GCM with an owner-readable master key. The service runs as a non-root user with a read-only container filesystem and restricted mounts.

LAN access requires an existing account and TLS. Only the public local CA certificate is transferred to client devices; the private CA stays outside the running dashboard's mounts. You approve certificate trust yourself. No firewall rule, router forwarding, public tunnel or remote deployment is created automatically. Read [SECURITY.md](SECURITY.md) for implemented boundaries and practical limits.

## Data leaving the host

Core dashboard functions and bundled logo rendering are local. Automatic ISP identification is off by default. When enabled without a manual provider override, the server checks for a public IPv4 change every **60 seconds** through `https://api.ipify.org?format=json`. It asks `https://ipwho.is/` for provider metadata at startup, after an exit-IP change, on lookup errors or when the six-hour metadata cache expires. The explicit refresh button requests a fresh provider lookup. Service errors and rate limits can delay updates. These are the services' documented [ipify](https://www.ipify.org/) and [ipwho.is](https://ipwhois.io/documentation) endpoints.

Both services see the server connection's public IP; no device inventory or integration credentials are sent. The change-check IP stays in transient memory and is omitted from stored settings and API responses. The displayed provider describes the public exit network, so VPNs and wholesale networks can differ from a retail ISP. A manual label remains until the user explicitly selects automatic detection; a successful lookup then clears the saved provider override.

A configured remote AI provider receives chat, selected telemetry and approved file-read contents. Ollama inference can stay on your own network. Image/dependency downloads occur when Docker builds the application.

## Documentation

- [Terminal installation and client trust](START-HERE.md)
- [Integration and pairing guide](docs/integrations.md)
- [Troubleshooting, updates and backups](docs/operations.md)
- [Contributing](CONTRIBUTING.md) and [security reporting](SECURITY.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)
- [Publish the repository, docs and v1.0.0 release](docs/RELEASING.md)
- [Pre-publication checklist](docs/PUBLISH-CHECKLIST.md)

`docs/index.html` is a standalone documentation website with local assets. To publish **only the documentation**, optionally select GitHub Actions in the repository's Pages settings and manually run **Publish documentation**. The supplied workflow has no push trigger. Alternatively, configure Pages to serve the `main` branch's `/docs` folder. GitHub Pages cannot run this Python service and is not a dashboard hosting option. [GitHub explains these publishing choices](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site).

## Upload this source to GitHub

Create an empty repository in your own GitHub account. Review this source folder, then use GitHub's file upload or run these commands yourself:

```sh
git init
git add .
git commit -m "Release HomeBase OS v1.0.0"
git branch -M main
git remote add origin YOUR_REPOSITORY_URL
git push -u origin main
```

This package creates no remote repository and performs no push or publication. The `ci.yml` workflow runs fixture-based Python/UI tests and an ARM64/AMD64 image build after upload; it does not push container images. Workflows and image builds have not been run on GitHub merely by generating this folder.

## Release and license

HomeBase OS remains version **1.0.0**. Source is provided under the [MIT license](LICENSE), with [separate third-party notices](THIRD_PARTY_NOTICES.md). Real hardware pairing, Raspberry Pi performance and LAN reachability must be validated on the target home network. The project does not claim universal smart-TV support or an independent security audit.
