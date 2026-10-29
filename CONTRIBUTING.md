# Contributing

Start with the [README](README.md), [terminal installation guide](START-HERE.md) and [integration guide](docs/integrations.md). The public distribution targets Raspberry Pi OS Lite 64-bit with Docker. Application code is Python; the responsive interface uses browser-native JavaScript modules and local assets.

## Development

1. Work on a branch in a clean checkout. Use temporary test credentials and fixture devices.
2. Keep integration reads separate from actions. A proposed action must never execute until the authenticated user approves its exact target and payload.
3. Run the checks below, or use the included GitHub Actions workflow. Python 3.12 and the packages in the requirement files are used in CI. Node is a test dependency only; the application has no Node runtime dependency.

```sh
python -m unittest discover -s tests -v
node --test tests/test_ui.mjs
bash -n scripts/setup-pi.sh
```

The CI workflow also builds the Docker image for Linux ARM64 and AMD64 without pushing it to a registry. Passing unit tests or emulated image builds does not establish real-device compatibility.

## Changes and reviews

Describe the concrete problem, resulting behavior, validation and hardware limits. Keep secrets, local addresses, personal screenshots and runtime data out of commits. Preserve accessibility, narrow-screen layouts and reduced-motion support. Do not add third-party CDNs, analytics, public tunnels or implicit device control.

When adding a TV or camera adapter, document supported firmware/protocols, authorized pairing, exact commands and a controlled fixture. Never claim universal device support. New logo assets need an authentic source and a credit in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) or the linked asset credits.

The documentation website is plain HTML/CSS in `docs/`. Preview it with an existing static server or open `docs/index.html`; publishing its optional Pages workflow requires a maintainer's explicit action. It does not deploy the dashboard.

## Public source export

```sh
python scripts/export-github.py --output releases/homebase-github
```

The exporter uses an allowlist, replaces local documentation with the public Pi guide, excludes native Mac launchers and runtime state, and scans the result before creating the destination. It refuses to overwrite a non-empty directory. Review the resulting files before uploading them. The local Mac distribution and its guides can remain separate from the public Pi profile.

Report security issues privately using [SECURITY.md](SECURITY.md).

## Rebuild the installation website

Use a project virtual environment: `python -m pip install -r requirements-docs.txt`, then run `python scripts/build-docs.py`. The generated `docs/setup.html` is tracked, so visitors and Raspberry Pi installations need no Markdown build dependency. Update `docs/INSTALL.md` as the guide source.
