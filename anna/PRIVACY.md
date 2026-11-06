# HomeBase OS for Anna: privacy

Version 0.1.2 · 6 November 2026

HomeBase runs on your own hardware. This Anna edition connects that private server through an explicitly enabled local bridge. It is not a wholly local-only interface.

## Data sent through Anna

Returned dashboard data can include device names/private IP addresses, host/network telemetry, integration names/status, server measurements, activity, Home Assistant entity state, assistant messages/proposals and requested command arguments/results. These pass through Anna's service and are subject to its own terms and privacy policy.

Camera images are a separate opt-in, off by default. If enabled, requested authenticated JPEG snapshots pass through Anna. The connector does not automatically stream cameras. Other features work with image sharing disabled.

## Data that stays local

Enter account passwords, authenticator codes, TV pairing codes, integration passwords and AI-provider keys only in local HomeBase. The Anna edition blocks those forms. The bridge rejects authentication/security/pairing/credential-management routes and credential-bearing request fields, and removes known secret fields from responses.

The connector creates a separate normal HomeBase session. Its cookie and CSRF value remain in local process memory and local HTTPS traffic, never in Anna's UI or a bridge credential file. HomeBase's existing encrypted vault is not copied or published.

## Permission and revocation

Command access and camera-image access default to off. The owner can enable them at `https://localhost:8420/anna-connect.html` after normal authentication. Commands retain HomeBase's per-device and action-approval rules. Connecting does not authorize the assistant to scan, control devices, approve proposals or edit files without the user's request.

Select **Disconnect and revoke access** there to invalidate the separate bridge session. Your normal browser session is preserved. Restarting HomeBase and session expiry can require reconnecting. Revocation prevents future requests; it cannot remove data already processed by Anna.

## Hosting and other services

The connector does not expose HomeBase publicly or change router, firewall, certificate trust or MFA settings. It declares no unrestricted shell/filesystem tool. Independently configured HomeBase integrations/providers retain their own data practices; ISP lookup or remote AI providers may contact their configured services.

The maintainer receives no telemetry through a separate HomeBase analytics service. Review and redact screenshots/logs before sharing them in support issues.

## Contact

Use https://github.com/Grillmaster353/homebase-os/issues for non-sensitive questions. Never post passwords, keys, authenticator codes, private camera images or runtime backups. See the repository's SECURITY.md for reporting guidance.
