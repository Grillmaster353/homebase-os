# Integration guide

HomeBase OS v1.0.0 uses real protocol clients and reports connection errors. Discovery and an attractive control do not establish authorization or universal device support.

## Network discovery and identity

Read-only discovery uses the local neighbor table, SSDP and supported ONVIF advertisements. The Pi LAN setup enables it at startup; otherwise start a scan explicitly. Refreshes occur every 60 seconds and pause when the host changes network. A cached neighbor is unverified, rather than automatically offline.

Throughput is bytes measured on the host interface, including other host applications. Gateway latency is a local round trip when ICMP is available. There is no automatic external bandwidth test.

Router manufacturer matching uses a bundled extract of IEEE registry prefixes; it does not submit MAC addresses to a lookup service. A registered manufacturer can differ from a retail label. Automatic ISP identification is off by default. When enabled without a manual override, a 60-second check contacts `https://api.ipify.org?format=json` to detect an exit-IP change. Provider metadata comes from `https://ipwho.is/` at startup, on an IP change or lookup error, at six-hour cache expiry, or after an explicit provider refresh. Errors and service limits can delay checks. Both services see the connection's public IP; no local device inventory or integration credentials are sent. The check IP is held only in memory and excluded from saved settings and API responses. A VPN or wholesale exit can differ from a retail ISP. A manual provider name remains until the user explicitly selects automatic detection; a successful explicit detection clears the saved override. Router names remain independently editable. No logo CDN is used.

## Home Assistant

Supply the local URL and an authorized long-lived access token. The REST client reads entities and proposes service calls for supported domains, scenes and automations. HomeBase OS does not install Home Assistant or edit its configuration files. Create automation rules in your existing Home Assistant, then run their supported services here after approval. The protocol follows the [Home Assistant REST API](https://contributors.home-assistant.io/core/internals/rest-api/).

## Smart televisions

Direct TV adapters pair locally without requiring Home Assistant. Use the adapter supported by your model and firmware, complete its physical permission/code flow, then approve commands separately. See [TV protocols and pairing](TV.md) for the exact matrix and limitations.

The Home Assistant route supports televisions already paired in Home Assistant, using the actual `media_player.*` entity. Navigation needs a compatible `remote.*` entity. Available services differ by that integration; selecting a vendor name does not add unsupported commands.

Direct protocol coverage is defined by the adapters shipped in this release. Read the connection form's protocol and pairing instructions. Hisense devices may use Google/Android TV, Roku or another platform, and the correct platform matters more than the brand label. Pairing does not bypass a TV's physical permission prompt. Unsupported models should retain an honest unavailable state or use an already authorized Home Assistant entity.

## Hikvision cameras

Supply the camera/NVR's private URL, authorized username/password and ISAPI channel (commonly `101` for channel 1 main stream). The client uses supported Digest authentication and requests `/ISAPI/Streaming/channels/{channel}/picture` for a JPEG. Model/firmware/API permissions vary. ONVIF discovery may need to be enabled in the camera's own settings and does not replace credentials. This release does not bundle live RTSP transcoding, recording or an authorization bypass.

## Servers and Raspberry Pi

Host telemetry is read from the machine/container running HomeBase OS. For other machines, configure an existing Prometheus `node_exporter` endpoint, normally port `9100` and `/metrics`; an optional bearer token and configured metrics path are supported. CPU utilization needs two counter samples. Installing or enabling an exporter is a separate action on that server.

## AI and approved workspace files

Configure an already running Ollama server and installed tool-capable model, or an explicit compatible provider endpoint. Ollama uses the [chat API](https://github.com/ollama/ollama/blob/main/docs/api.md); actual speed depends on the host and selected model. A model server or model is not installed automatically.

The assistant sees bounded chat and selected telemetry. A file read must be proposed and approved before its contents can inform follow-up edits. A configured remote provider can receive those approved contents. Each proposed file write, TV command or Home Assistant service must be approved separately and executes once. File paths stay in the configured workspace; arbitrary shell commands and self-approval are unavailable.

See [SECURITY.md](../SECURITY.md) and [third-party notices](../THIRD_PARTY_NOTICES.md).
