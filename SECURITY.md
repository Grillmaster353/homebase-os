# Security policy

HomeBase OS v1.0.0 is intended for a trusted private home network. It has not undergone an independent security audit. There is no public hosting mode, cloud account recovery service or supported internet-facing deployment.

## Reporting a vulnerability

Use this repository's **Security → Advisories → Report a vulnerability** option when private reporting is enabled. If that option is unavailable, open an issue asking the maintainer for a private reporting channel without including exploit details, credentials or private network information. Do not post an encrypted vault, encryption key, private certificate key, camera credentials, public IP address or unredacted logs.

Include the version, deployment method, affected endpoint, reproduction steps using test data, expected result and impact. Use a minimal local fixture whenever possible. No response-time or support guarantee is promised.

## Implemented boundaries

- Administrator passwords use salted scrypt hashes; there is no default password.
- Session cookies are HttpOnly and SameSite=Strict. Authenticated mutations require a same-origin request and CSRF token. Authentication is rate limited, sessions expire, and optional TOTP adds a second factor.
- Credentials and settings use AES-256-GCM with a separate local master key. The service's runtime data has restricted filesystem permissions.
- A LAN listener requires an existing account and TLS. The container runs as UID/GID 10001, drops capabilities and uses a read-only root filesystem.
- The setup-only private CA is not mounted in the running dashboard. Client trust changes are always manual.
- Integration clients validate local/private destinations, pin resolved addresses, reject redirects and bound responses. An explicitly selected remote AI provider uses HTTPS.
- Device commands and assistant file operations require a separate, one-time human approval. Files are confined to the configured workspace; arbitrary shell execution is unavailable.

## Operational limits

A host administrator or a person who can read both the vault and its master key can recover stored integration credentials. Docker access is privileged host access. Protect the host, volumes, backups and private CA accordingly. A local TLS certificate proves the configured hostname/IP only when the client trusts the correct CA. Do not bypass certificate warnings or add a public router port-forward.

Discovery cannot authorize a device. Use accounts and pairing methods supplied by the device manufacturer. Remote AI prompts may contain selected telemetry, chat history and file contents you explicitly approved for reading; choose local Ollama inference when that data must stay local.

The encrypted dashboard connection does not make every device protocol encrypted. The implemented Samsung/LG local WebSocket channels and Roku ECP require a separate cleartext-LAN opt-in. Android TV and Apple TV use their supported pairing/client-identity protocols. Read [docs/TV.md](docs/TV.md) before enabling control.

Security changes and dependency updates should preserve fail-closed behavior and include regression tests. See [CONTRIBUTING.md](CONTRIBUTING.md).
