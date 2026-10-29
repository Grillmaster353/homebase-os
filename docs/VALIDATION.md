# Release verification — HomeBase OS v1.0.0

Local checks on 29 October 2026; GitHub CI confirmed on 30 October 2026:

- 110 Python tests passed: authentication/CSRF, encryption, MFA, workspace-name persistence, discovery parsers, direct TV protocol fixtures, approval boundaries, private transport checks, source export privacy, Pi setup sequencing, and minute-by-minute ISP change scheduling.
- 26 offline UI tests passed, including provider logos, TV removal controls, direct connection forms, a live receive graph without a bandwidth-test action, and automatic GitHub Pages source/download links.
- Browser checks exercised workspace rename and reload, direct TV PIN pairing against an isolated fixture, removing a saved TV, provider SVG loading, and responsive dashboard/documentation layouts. Fixtures contain example data; they do not establish physical-device compatibility.
- The existing local HTTPS service started successfully with its account, MFA configuration, integration secrets, and vault key preserved. Unauthenticated telemetry remained blocked.

The public source includes no account, password, runtime vault, private certificate, private CA, assistant workspace, or personal screenshots. Its export manifest records SHA-256 hashes for the included source files.

GitHub Actions successfully ran the fixture checks and built Linux ARM64 and AMD64 Docker images without publishing them: [verification run](https://github.com/Grillmaster353/homebase-os/actions/runs/40843504330), application source revision `449f0fb`. Docker remains unavailable on the development Mac, so no local Docker run is claimed. No physical Raspberry Pi deployment or real TV pairing/control is claimed. Target hardware and network checks remain necessary. This release has not undergone an independent security audit.
