# HomeBase OS for Anna · 0.1.2

Your HomeBase dashboard in Anna, using real data from your own server. This early release supports a native macOS HomeBase installation with the official local Anna Agent. HomeBase's standalone Raspberry Pi/Docker installation remains available in the main project; the Anna connector's Docker/remote-host deployment has not been validated.

## Requirements

- An Anna account and the official [Anna Agent](https://anna.partners/download), signed in on the Mac. Select that local Agent in Anna. A cloud Agent cannot access this private connector.
- A native HomeBase installation from this repository, with an administrator account and HTTPS at `https://localhost:8420`.
- Python 3 on the host. The bridge uses only its standard library and installs no packages. HomeBase uses its normal project dependencies.
- The existing `certs/homebase-rootCA.crt`, matching the server's HTTPS certificate. Never share private keys, `.data` or `.setup-ca`.

## Connect an existing installation in your browser

1. Back up your private runtime data separately. Update the HomeBase source, preserving `.data`, certificates and workspace, then restart HTTPS.
2. Open **https://localhost:8420/anna-connect.html** on the same Mac as HomeBase and Anna Agent.
3. Sign in with your normal HomeBase account, password and current authenticator code. These fields belong to your local server, never the Anna app.
4. Choose whether to allow supported commands. Leave camera sharing off unless you want requested camera JPEGs to pass through Anna. Both grants default to off.
5. Select **Connect HomeBase to Anna**.
6. Open **HomeBase OS** in Anna and select **Check connection**. Actual statistics and saved integrations should load. Pair devices and enter integration/provider credentials in local HomeBase, then refresh Anna.

No Terminal setup is needed to connect an existing configured installation. To revoke, return to that local page and select **Disconnect and revoke access**. This invalidates the separate bridge session while preserving your normal browser session. HomeBase restarts and session expiry require reconnecting. The connector does not bypass two-factor authentication.

## New native macOS installation

Initial server setup needs a terminal. See the main repository README for prerequisites and optional TV packages. Install only dependencies you understand and approve.

```sh
git clone https://github.com/Grillmaster353/homebase-os.git
cd homebase-os
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-tv.txt
./scripts/start.sh
```

Open `http://127.0.0.1:8420` locally and create your own administrator account. There is no shared account, default password or hosted registration service. Stop this initial service after account setup.

Generate local TLS material only if you do not already have it:

```sh
.venv/bin/python -m app.lan_setup --ip 127.0.0.1
```

This creates a private local CA and a localhost certificate. Verify and trust **only your own public CA certificate** through normal browser/system certificate management. This is your explicit security choice; setup never changes trust automatically. Do not bypass an unexpected certificate warning or publish a private key.

Start private HTTPS:

```sh
./scripts/start.sh --host 127.0.0.1 --port 8420 --data-dir .data --cert certs/server.crt --key certs/server.key
```

Keep it running and follow the browser steps above. This listener is private to your Mac. LAN dashboard access is a separate opt-in in the main project. Anna needs no port forwarding or public tunnel.

## Supported features

- Actual traffic, latency, charts, inventory and activity.
- Supported, already paired TV remotes and keyboard after command opt-in; physical device/protocol support still applies.
- Configured Home Assistant entities/actions, server monitoring and HomeBase AI provider/assistant.
- Requested camera JPEGs only after separate image-sharing opt-in.

Account/MFA administration, pairing and all credentials stay in local HomeBase. The Anna UI blocks those forms and the bridge rejects their routes. Assistant/file proposals retain HomeBase's configured workspace and approval restrictions. No arbitrary shell, filesystem or external-URL tool is declared.

## Privacy and security

Read [PRIVACY.md](PRIVACY.md). Selected device names/IPs, telemetry, assistant messages and requested commands pass through Anna. This edition is not entirely local-only. Account passwords, authenticator codes, session credentials and stored device secrets stay on your HomeBase host.

The connector enforces an API allowlist, HTTPS/hostname verification against your CA, private-address-only targets, no redirects and normal HomeBase authentication/CSRF. Its separate session stays only in memory. The owner-only socket directory is mode 0700 and socket mode 0600. Browser enrollment is limited to authenticated loopback HTTPS with CSRF. It does not relax CORS, firewall, TLS trust or MFA.

## Build and tests

From the repository root:

```sh
python3 anna/build.py
.venv/bin/python -m unittest discover -s anna/tests
node --test anna/tests/transport.test.mjs
.venv/bin/python -m unittest discover -s tests
```

For a separate source checkout, use `build.py --source /path/to/homebase-os` and set `HOMEBASE_SOURCE` to that same path for bridge tests. Tests need HomeBase's project dependencies; the bridge itself does not.

The build copies web source, creates relative assets and an Anna transport, and excludes runtime state and local credential-entry pages. The launcher resolves Anna's install symlinks and isolates Python. Tests cover real temporary HTTPS login/CSRF/logout, owner-only socket relay, route/secret/camera restrictions, multi-frame JSON-RPC and the installed symlink launcher. They do not simulate successful physical-device commands.

## Review and release

Developer console: https://anna.partners/developer?app=817 · qualified reference `@grillmaster/homebase-os`.

The developer finalizes the static bundle, installs it on a local Agent, verifies telemetry and submits the exact candidate. Submission is not public availability: Anna must approve it before App Store publication. The version changelog records acceptance results and any remaining platform blockers.

The versioned bridge archive is supplied in `releases/0.1.2/`; Anna's binary distribution pins its checksum for supported macOS hosts. Other platforms and container connector deployment require separate validation before being advertised as supported.

The existing MIT license and third-party brand notices apply. No private account data or certificates are included.
