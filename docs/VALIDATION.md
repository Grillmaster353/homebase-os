# Release verification — HomeBase OS v1.0.0

Local checks on 29 October 2026; GitHub CI confirmed on 30 October 2026:

- 110 Python tests passed: authentication/CSRF, encryption, MFA, workspace-name persistence, discovery parsers, direct TV protocol fixtures, approval boundaries, private transport checks, source export privacy, Pi setup sequencing, and minute-by-minute ISP change scheduling.
- 26 offline UI tests passed, including provider logos, TV removal controls, direct connection forms, a live receive graph without a bandwidth-test action, and automatic GitHub Pages source/download links.
- Browser checks exercised workspace rename and reload, direct TV PIN pairing against an isolated fixture, removing a saved TV, provider SVG loading, and responsive dashboard/documentation layouts. Fixtures contain example data; they do not establish physical-device compatibility.
- The existing local HTTPS service started successfully with its account, MFA configuration, integration secrets, and vault key preserved. Unauthenticated telemetry remained blocked.

The public source includes no account, password, runtime vault, private certificate, private CA, assistant workspace, or personal screenshots. Its export manifest records SHA-256 hashes for the included source files.

GitHub Actions successfully ran the fixture checks and built Linux ARM64 and AMD64 Docker images without publishing them: [verification run](https://github.com/Grillmaster353/homebase-os/actions/runs/40843504330), application source revision `449f0fb`. Docker remains unavailable on the development Mac, so no local Docker run is claimed. No physical Raspberry Pi deployment or real TV pairing/control is claimed. Target hardware and network checks remain necessary. This release has not undergone an independent security audit.

## Telstra artwork update — 30 October 2026

111 Python tests and 27 offline UI tests pass after adding official local Telstra vectors. Telstra and BigPond aliases resolve to the same artwork ID. The live Mac dashboard was reloaded and its automatically detected Telstra badge visibly rendered the bundled logo. When a router logo is also available, the provider retains its own icon beside its name. No account settings, credentials or device controls were changed.

## Appearance update — 30 October 2026

The reference-inspired layout keeps all four overview statistics, six overview sections, routes, integration forms and action hooks. Dark colors remain; an optional light mode is saved in browser-local storage. 111 Python tests and 39 offline UI/theme tests pass. Browser checks cover 375px, 768px and 1440px layouts, every mobile route, a saved theme after reload, and an unsaved open form surviving a cross-tab theme change. No browser console errors were observed. All backend source hashes are unchanged.

## v1.1.0 — The Liquid Glass Update, 1 November 2026

The release packages the pronounced liquid-glass presentation in both themes, rounded device filters and the current account/TV integration code. Its interface remains local and preserves the existing pairing, action-approval and credential boundaries.

- The v1.1.0 preparation source passed 177 Python fixture tests and 58 offline UI/theme tests. The appearance commit also passed the fixture jobs and Linux ARM64/AMD64 Docker image builds in [GitHub Actions](https://github.com/Grillmaster353/homebase-os/actions/runs/41291799470).
- The actual Mac HTTPS service served the verified stylesheet. Safari was checked against the running dashboard. Isolated visual-only pages generated from the same UI functions were checked at 390 px and 1440 px in both themes, without horizontal overflow. The rounded Devices rail was verified at 14 px; the major glass panes used the intended transparent backgrounds and backdrop filters.
- Public light/dark previews contain illustrative fixture telemetry and generic names, not a real household inventory. They show the actual application renderer and stylesheet, not a generated design mockup.
- Export audits exclude runtime accounts, vaults, private keys, certificates and assistant files. Release source archives include a manifest and a separate SHA-256 checksum.

Physical Raspberry Pi fit/performance and universal device compatibility are not established by these checks. Samsung pairing can still fail on some models; this release does not claim to fix that issue. Model/firmware requirements in docs/TV.md still apply. No new independent security audit is claimed. Release-preparation checks and the versioned commit's CI results should be reviewed before publication.
