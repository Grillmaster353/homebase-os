# Anna release acceptance · 0.1.2

Checked 6 November 2026 on the owner's macOS installation with official Anna Agent 1.1.0-beta.46.

- HomeBase server suite: **180 passed**.
- Existing interface checks: **58 passed**.
- Anna bridge suite, including installed symlink/Python isolation: **10 passed**.
- Anna transport checks: **4 passed**.
- Public versioned bridge downloaded and installed by the local Anna Agent with the declared SHA-256 checksum.
- Normal local HomeBase sign-in and browser enrollment completed. Separate bridge session returned authenticated status and real overview data.
- HomeBase OS opened from Anna's app launcher, inside the actual Anna runtime, and displayed changing live traffic and charts.
- Network and Smart TVs pages opened. Settings showed the local administration links and command/image grants, without password fields.
- Both light and dark themes rendered. Settings and overview had no horizontal overflow at a 388 CSS-pixel content width; this was a browser layout check, not a physical iPhone test.
- Camera-image sharing was off. No camera JPEG, network discovery, physical-device command or file-changing assistant action was used in acceptance testing.

Anna app **817**, version **0.1.2**: static bundle finalized with **50 files**, and the review candidate was explicitly switched to this version. Public App Store availability still requires Anna's approval. A submitted candidate is not an approved/public release.

This preview requires native macOS HomeBase, Python 3 and a local Anna Agent. The archive is architecture-independent Python/shell source; actual Agent installation was verified on Apple Silicon. Intel macOS uses the same source archive but was not separately hardware-tested. Docker/remote-host Anna connector deployment is not advertised as supported. Physical integrations still depend on actual models, configuration, pairing and the existing HomeBase approval rules.

Public bridge SHA-256: `12701ce28dcf87ecf6a68222299d74ede957d55edef42108d5fbd40368a6673e`.

This report contains no private hostnames, addresses, credentials or camera images.
