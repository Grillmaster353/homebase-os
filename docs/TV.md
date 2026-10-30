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
| Hisense | Choose the actual operating system: Google/Android TV, Roku, or VIDAA. | The brand alone does not select a protocol. An attached HDMI streaming device pairs separately from the TV’s built-in system. |
| Hisense VIDAA | Compatible local MQTT-over-TLS service, normally port 36669. Supply an authorised client credential profile and verified TLS trust, then request and enter the four-digit VIDAA PIN. | Advanced, firmware-dependent support. No shared vendor private keys or broker passwords are bundled. A PIN alone cannot authenticate the initial connection. The adapter does not enable remote services or change TV security settings. The Power button is a toggle, not separate power-on/off commands. Wake from standby depends on the TV keeping its network service available; channel buttons depend on the current TV/tuner context. |
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
3. For Android TV, enter its six hexadecimal characters. If it is an HDMI
   Chromecast, select that HDMI input to see the Android pairing screen. For
   Apple TV or a configured VIDAA connection, enter its four-digit PIN. For Samsung or LG, accept the on-screen prompt and choose
   **Finish pairing**. For Roku, confirm that you enabled its local control.
4. Homebase encrypts the resulting token, certificate/key, or protocol
   credentials. They are not returned in normal integration API responses.
5. Remote buttons require approval by default. To use a paired direct TV as a
   normal remote, edit that TV and enable **Allow immediate remote buttons for
   this TV**. Save once; subsequent manual clicks go directly to that TV.
   Pairing alone does not enable this permission. Assistant and automation
   proposals still require separate approval.

Pairing sessions expire after three minutes and are bound to their saved
integration and resolved destination. Changing the address or protocol requires
starting a fresh pairing session. Cancelled and expired sessions close their
network handles and remove temporary client-key files.

## Manual remote buttons

Immediate manual control is **off by default** for new connections. Open a TV's
settings, tick **Allow immediate remote buttons for this TV**, and save. The
permission applies only to that saved direct TV after pairing. Buttons still
require your signed-in HomeBase session and a supported command. Other TVs and
Home Assistant controls keep their own approval behavior.

Untick the option and save to restore approval for each manual remote click.
Changing the TV endpoint or protocol requires pairing and permission again.
Assistant and automation proposals continue through the approval queue, even
when immediate manual buttons are enabled. Activity records distinguish those
manual commands from approved proposals.

On VIDAA, **Power** sends the real power-toggle key. It can switch the screen
off; waking it depends on standby/network settings that you configure on the TV.
**CH +** and **CH −** send channel-up/down keys. These work where the TV supports
channel navigation, such as live TV; streaming apps may ignore them. Play and
pause are separate buttons with separate commands. A delivery acknowledgment
does not prove the display changed state.

## VIDAA credential profile

VIDAA firmware can require an authorised mobile-app client identity before its
MQTT broker will accept a connection or display a PIN. HomeBase does not create
that vendor identity, extract credentials from another device, or distribute
universal client certificates, private keys, or service passwords. Obtain an
identity you are authorised to use through your supported vendor or existing
integration. If your TV provides no supported way to obtain one, use its official
remote app or a supported Home Assistant integration instead.

1. Add **Hisense TV**, choose **Direct**, and explicitly select **Hisense VIDAA**.
   Discovery can preselect VIDAA when the TV advertises a positive VIDAA marker;
   an ambiguous Hisense advertisement leaves the platform for you to choose.
2. Use the TV's private address. Port `36669` is the normal MQTT/TLS default.
3. Paste a JSON object into **VIDAA credential profile · advanced** with the
   following fields. This is credential data, not a file path or a shell command.

| Field | Purpose |
| --- | --- |
| `clientId` | Stable MQTT client identity authorised for your remote profile. |
| `username`, `password` | Authorised MQTT broker login for that identity. HomeBase does not guess these. |
| `certificatePem`, `privateKeyPem` | Required PEM client certificate and matching private key for this adapter's mutual TLS connection. JSON strings use `\n` for PEM line breaks. |
| `caPem` | Trusted CA PEM used to verify the TV's TLS certificate, when its certificate supports normal verification. |
| `serverFingerprint` | Explicitly trusted SHA-256 server-certificate fingerprint for a TV that cannot be verified with your CA. Verify it through a trusted source before use. |
| `authMode` | `modern` for the token-based pairing flow, or `legacy` only for compatible older tokenless firmware. |

Supply `clientId`, `username`, `password`, `certificatePem`, and `privateKeyPem`,
plus verified TLS trust using `caPem` or `serverFingerprint`. A fingerprint is
64 hexadecimal characters, with optional colon separators. CA-only mode uses
normal host/IP certificate verification; explicit pinning verifies the approved
leaf certificate before sending the MQTT login.
Do not copy an unverified certificate fingerprint from an unknown website or
paste your dashboard password into this profile. Secrets stay in the encrypted
vault. Editing an existing connection never fills the saved profile back into
the browser; leaving the profile box blank keeps the saved identity.

4. Save, choose **Pair TV**, and start pairing. Keep the television awake. A
   rejected MQTT login occurs before the PIN screen, so repeatedly entering a
   PIN will not fix a missing or incompatible client identity.
5. Enter the four-digit code shown on the VIDAA screen and complete pairing.
   HomeBase reports paired only after the protocol confirms authorisation.
   Remote keys need separate approval unless you enable immediate manual
   buttons for this saved TV. Assistant proposals always need approval.

Changing the endpoint, protocol or identity requires fresh pairing. Do not reuse
old tokens merely because another TV has the same brand or display name. Never
put a completed credential profile in a public repository or release archive.

## Protocol security

The HTTPS dashboard, encrypted vault, and local TV protocol are different links.
Android TV Remote v2 uses a paired client certificate and its PIN protocol. Apple
TV uses the supported pairing and credential protocols implemented by `pyatv`.
Their device protocols do not use ordinary public-web certificate identity.
VIDAA uses the owner-supplied TLS trust profile. A configured certificate pin is
checked before MQTT credentials are sent; there is no silent unverified-TLS
fallback. Client keys are stored in the encrypted vault, with private temporary
files used only while loading the TLS identity.

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

SSDP identifies advertised Samsung, Roku, webOS and positively identified VIDAA
candidates. Bounded descriptions from the responding device can supply its real
name and model. A generic Hisense brand marker remains ambiguous. Optional mDNS
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
- [VIDAA MQTT protocol reference](https://github.com/warrenrees/pyvidaa)
- [VIDAA official support](https://www.vidaa.com/support/)

## Smart TVs remote and keyboard

Use **Smart TVs** in the sidebar for saved remotes. **Devices** keeps network discovery and monitoring without remote-control panels. Choose a television, then use the circular direction pad, Home/Back, volume and channel rockers, media controls, and **123 & more controls**. The server supplies each direct platform’s command allowlist, so unsupported keys are not shown. An explicit power toggle is labelled as a toggle; waking a sleeping TV still depends on its network/standby support.

On a laptop, Arrow keys navigate, Enter selects, and Escape goes back while the remote page is active and no text field is focused. On a phone, the controls use large touch targets and the keyboard moves below the remote. The same dark/light appearance applies.

To type, focus a supported text/search field on the television first. Use the digital keyboard or your device’s own keyboard, then press **Send to TV**. Direct text requires a paired TV and its one-time immediate manual-control permission. Text is bounded to 512 characters, stays in memory until sent, is cleared after successful sending or sign-out, and is excluded from activity logs. Android, Apple TV, Samsung, Roku, LG webOS and VIDAA use their separate local input protocols; app and firmware support varies. Sending reports transport delivery, not proof of the resulting on-screen text. Home Assistant connections retain their existing supported media/navigation controls.

VIDAA text uses the platform service’s native `txtinputdata` and browser `bwsinputdata` insertion routes, documented by the [maintainer implementation](https://github.com/stevene1919/hisense_vidaa/blob/main/custom_components/hisense_vidaa/tv/actions.py). It does not install a keyboard, enable ADB, or alter TV security settings.
