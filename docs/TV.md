# Direct TV control

Homebase can control supported TVs directly on the local network. New TV
connections can use **Direct connection**; an existing Home Assistant connection
continues to work. A brand name by itself does not identify the TV's operating
system, available remote service, or supported commands.

## Compatibility

| TV / platform | Direct connection and authorization | Limits |
| --- | --- | --- |
| Samsung Smart TV | Modern local Samsung WebSocket remote API, normally port 8001. Explicitly accept Homebase on the TV. The returned remote token is encrypted in the vault. | Older Samsung generations and models exposing only a different or HTTPS-only API may not work with this adapter. The implemented local WS channel requires explicit cleartext-LAN permission. |
| Google TV / Android TV | Android TV Remote Service v2, using `androidtvremote2`. Start pairing, enter the six-character TV code, then save the client identity in the encrypted vault. API port 6466 and pairing port 6467 are usual defaults. | The TV must have a supported Android TV Remote Service enabled. ADB and developer mode are not required. No voice/microphone features are enabled by Homebase. |
| Hisense | Choose the actual operating system. Google/Android TV models use Android TV Remote v2; Roku models use Roku ECP. | A Hisense VIDAA-only TV is not claimed as supported by either adapter. Do not select Android merely because the brand is Hisense. |
| Apple TV | `pyatv` discovers supported services and pairs with a four-digit PIN. Companion is preferred when it supports pairing; AirPlay is a fallback. | Availability varies by tvOS version and protocol. Some media/volume commands need an additional supported service or a device configuration outside Homebase. Legacy PIN-entry-on-TV flows are not handled by this form. Mute is not universally supported. |
| Roku / Roku TV | Local ECP on port 8060. Enable **Control by mobile apps** on the Roku yourself, then explicitly authorize that local connection in Homebase. No PIN is invented. | Power and volume depend on the Roku model and TV/HDMI settings. Play/pause is a toggle. Developer-only ECP APIs, arbitrary app launching, and unsupported commands are not exposed. |
| LG webOS | Local SSAP/WebSocket, usually port 3000. Accept Homebase's local registration prompt on the TV; its returned client key is encrypted in the vault. | Firmware and remote-permission support vary. The adapter requires explicit cleartext-LAN permission; it does not bypass an HTTPS-only TV certificate. Deep-sleep power-on is not implemented. |

These are real protocol adapters, rather than universal compatibility claims.
They have controlled protocol/approval tests, but each physical TV and firmware
still needs pairing and verification on its own home network.

## Pairing

1. Find a TV in **Devices**, or add its private address and choose the actual
   protocol. Homebase does not need a Home Assistant address for a direct TV.
2. Choose **Pair TV**. Read the pairing notice and explicitly start it. Pairing
   may wake the TV or display a permission prompt.
3. For Android TV, enter its six hexadecimal characters. For Apple TV, enter its
   four-digit PIN. For Samsung or LG, accept the on-screen prompt and choose
   **Finish pairing**. For Roku, confirm that you enabled its local control.
4. Homebase encrypts the resulting token, certificate/key, or protocol
   credentials. They are not returned in normal integration API responses.
5. A remote button proposes an action. Review and approve that exact action
   before it is sent. Pairing alone does not approve future commands.

Pairing sessions expire after three minutes and are bound to their saved
integration and resolved destination. Changing the address or protocol requires
starting a fresh pairing session. Cancelled and expired sessions close their
network handles and remove temporary client-key files.

## Protocol security

The HTTPS dashboard, encrypted vault, and local TV protocol are different links.
Android TV Remote v2 uses a paired client certificate and its PIN protocol. Apple
TV uses the supported pairing and credential protocols implemented by `pyatv`.
Their device protocols do not use ordinary public-web certificate identity.

Samsung's implemented local WS API, LG's implemented local WS API, and Roku ECP
are **unencrypted local channels**. They require an explicit per-device opt-in
before pairing or commands. Use them only on your trusted, isolated home LAN.
Homebase never sends your dashboard password to a television. An entered HTTPS
address is not silently downgraded and certificate validation is not disabled to
make an unsupported TV work.

Discovery and read-only connection tests do not register a remote or send a
power/media/key command. Discovery does not guess credentials or change a TV's
settings. Endpoint validation restricts clients to local/private addresses and
pins library connections to the validated IP.

## Discovery and power

SSDP identifies advertised Samsung, Roku and webOS candidates. Optional mDNS
discovery recognizes Android TV Remote v2, Samsung, and supported Apple TV service
advertisements. Companion Link is also used by Macs and iPhones, so it is not
classified as an Apple TV without an Apple TV model marker. These queries run
only after discovery is authorized. Multicast must pass, and devices must be on
the visible local subnet. A sleeping TV, isolated VLAN, disabled remote service,
or blocked multicast can require manual addressing.

Power-on is possible only when the relevant TV service still listens or the
device supports the selected protocol's wake behavior. Homebase does not guess
Wake-on-LAN MAC addresses or enable wake/network settings on a TV. A command that
was sent is not proof that the display changed state. Unsupported per-protocol
commands are rejected instead of being translated to a different action.

## Runtime dependencies

The Docker image installs `requirements-tv.txt` during its build. This adds maintained client
libraries, including `androidtvremote2`, `pyatv`, `websockets`, and `zeroconf`.
There is no need for ADB, an Android SDK, a GUI, a microphone, or a browser on
Raspberry Pi OS Lite. Host package installation is never performed automatically
by the running dashboard.

## Protocol references

- [Android TV Remote v2 library and demo](https://github.com/tronikos/androidtvremote2)
- [pyatv scan, pair and connect API](https://pyatv.dev/development/scan_pair_and_connect/)
- [Samsung local WebSocket client reference](https://github.com/xchwarze/samsung-tv-ws-api)
- [Roku External Control Protocol](https://developer.roku.com/dev/docs/external-control-api)
- [LG webOS local remote reference](https://github.com/klattimer/LGWebOSRemote)
