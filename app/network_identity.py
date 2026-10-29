"""Honest network identity with local router evidence and opt-in ISP lookup.

No lookup occurs during construction or snapshot(). Public endpoints are fixed, HTTPS, and queried without LAN addresses,
device data or credentials. Automatic change checks use ipify.
API contracts: https://ipwhois.io/documentation and https://www.ipify.org/.
"""
from __future__ import annotations

import copy
import json
import ipaddress
import re
import threading
import time

from .integrations import safe_request


LOOKUP_URL = "https://ipwho.is/?fields=success,connection.isp,connection.org"
CACHE_SECONDS = 60
CHANGE_CHECK_URL = "https://api.ipify.org?format=json"
PROVIDER_CACHE_SECONDS = 6 * 60 * 60
DEFAULT_SETTINGS = {"ispName": "", "routerName": "", "allowExternalLookup": False}

# Aliases identify names actually reported by the lookup or supplied by the
# administrator. A router brand is never used to infer a retail ISP.
ISP_ALIASES = (
    ("cloudflare-inc", "Cloudflare", ("cloudflare",)),
    ("broadband-solutions", "Broadband Solutions", ("broadband solutions",)),
    ("aussie-broadband", "Aussie Broadband", ("aussie broadband",)),
    ("southern-phone", "Southern Phone", ("southern phone",)),
    ("telstra", "Telstra", ("telstra", "bigpond")),
    ("optus", "Optus", ("optus",)),
    ("exetel", "Exetel", ("exetel",)),
    ("iinet", "iiNet", ("iinet",)),
    ("superloop", "Superloop", ("superloop",)),
    ("tpg", "TPG", ("tpg",)),
    ("vodafone", "Vodafone", ("vodafone",)),
    ("belong", "Belong", ("belong",)),
    ("dodo", "Dodo", ("dodo",)),
    ("agl", "AGL", ("agl",)),
)
ROUTER_ALIASES = (
    ("tplink", "TP-Link", ("tp link", "tplink")),
    ("asus", "ASUS", ("asus", "asustek")),
    ("netgear", "NETGEAR", ("netgear",)),
    ("ubiquiti", "Ubiquiti", ("ubiquiti", "unifi")),
    ("cisco", "Cisco", ("cisco",)),
    ("mikrotik", "MikroTik", ("mikrotik",)),
    ("huawei", "Huawei", ("huawei",)),
    ("zte", "ZTE", ("zte",)),
    ("dlink", "D-Link", ("d link", "dlink")),
    ("fritzbox", "FRITZ!Box", ("fritz box", "fritzbox", "avm")),
    ("google", "Google Nest", ("google", "nest wifi")),
    ("eero", "eero", ("eero",)),
    ("nokia", "Nokia", ("nokia",)),
    ("sagemcom", "Sagemcom", ("sagemcom",)),
    ("technicolor", "Technicolor", ("technicolor",)),
    ("arris", "ARRIS", ("arris",)),
    ("linksys", "Linksys", ("linksys",)),
)


def _words(value):
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def canonical_name(name, kind="isp"):
    """Known logo IDs with a text fallback for every other provider or brand."""
    name = clean_name(name)
    if not name:
        return {"name": None, "id": "unknown"}
    normalized = " " + _words(name) + " "
    for identity, display, aliases in ISP_ALIASES if kind == "isp" else ROUTER_ALIASES:
        if any(" " + alias + " " in normalized for alias in aliases):
            return {"name": display, "id": identity}
    return {"name": name, "id": re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:80] or "unknown"}


def clean_name(value):
    if not isinstance(value, str):
        return ""
    return " ".join("".join(character for character in value if character.isprintable()).split())[:160]


def validate_settings(value):
    if not isinstance(value, dict):
        raise ValueError("Network identity settings must be an object.")
    for key in ("ispName", "routerName"):
        if key in value and (not isinstance(value[key], str) or len(value[key]) > 160):
            raise ValueError("Provider and router names must be text of at most 160 characters.")
    if "allowExternalLookup" in value and not isinstance(value["allowExternalLookup"], bool):
        raise ValueError("The external ISP lookup preference must be true or false.")
    return {**{key: clean_name(value[key]) for key in ("ispName", "routerName") if key in value}, **({"allowExternalLookup": value["allowExternalLookup"]} if "allowExternalLookup" in value else {})}


def router_identity(overview, settings):
    network = overview.get("network", {})
    gateway = network.get("gateway")
    device = next((item for item in overview.get("devices", []) if item.get("ip") == gateway), {}) if gateway else {}
    mac = network.get("gatewayMac") or device.get("mac")
    result = {"name": None, "id": "unknown", "source": "cached-gateway", "ip": gateway, "mac": mac, "status": "unknown", "detectedAt": None}
    if settings.get("routerName"):
        return {**result, **canonical_name(settings["routerName"], "router"), "source": "manual", "status": "manual"}
    if mac:
        try:
            from .router_ouis import lookup_router_brand
            match = lookup_router_brand(mac)
        except ImportError:
            match = None
        if match:
            result.update(name=match["name"], id=match["id"], source="cached-neighbor-oui", status="inferred", manufacturer=match.get("manufacturer"), ouiPrefix=match.get("prefix"), evidenceSource=match.get("source"), detectedAt=device.get("lastSeen"))
            result["note"] = "Registered manufacturer of the cached gateway MAC. This may be a virtual gateway or OEM, rather than the router's retail brand."
    if result["status"] == "unknown":
        result["note"] = "No recognized manufacturer in the cached gateway neighbor entry. Run local discovery or enter the router brand manually."
    return result


class NetworkIdentity:
    def __init__(self, settings_provider, overview_provider):
        self._settings_provider, self._overview_provider = settings_provider, overview_provider
        self._lock, self._lookup_lock = threading.RLock(), threading.Lock()
        self._check_lock = threading.Lock()
        self._public_ip = None
        self._checked_at = None
        self._last_check_attempt = 0
        self._stop, self._wake = threading.Event(), threading.Event()
        self._thread = None
        self._signature = None
        self._isp = None
        self._last_attempt = 0
        self._error = None

    def _settings(self):
        value = self._settings_provider()
        return {**DEFAULT_SETTINGS, **(value if isinstance(value, dict) else {})}

    @staticmethod
    def _network_signature(overview):
        network = overview.get("network", {})
        return tuple(network.get(key) for key in ("interface", "localAddress", "gateway"))

    def _refresh_network(self, overview):
        signature = self._network_signature(overview)
        with self._lock:
            if self._signature is not None and self._signature != signature:
                self._isp, self._last_attempt, self._error = None, 0, None
                self._public_ip, self._checked_at, self._last_check_attempt = None, None, 0
            self._signature = signature

    def snapshot(self, overview=None, *, prefer_detected=False):
        overview = overview if overview is not None else self._overview_provider()
        self._refresh_network(overview)
        settings = self._settings()
        with self._lock:
            isp = copy.deepcopy(self._isp) if self._isp else {"name": None, "id": "unknown", "source": "none", "detectedAt": None, "status": "unknown"}
            if isp.get("detectedAt") and time.time() - max(isp["detectedAt"], self._checked_at or 0) > CACHE_SECONDS + 5:
                isp["status"] = "stale"
            if self._error:
                isp.update(status="unavailable", message=self._error)
            detected_isp = copy.deepcopy(isp)
            if settings.get("ispName") and not prefer_detected:
                isp = {**canonical_name(settings["ispName"]), "source": "manual", "detectedAt": None, "status": "manual"}
        return {"isp": isp, "detectedIsp": detected_isp, "router": router_identity(overview, settings), "externalLookupEnabled": settings.get("allowExternalLookup") is True, "lookupProvider": "ipwho.is", "cacheSeconds": CACHE_SECONDS, "refreshSeconds": CACHE_SECONDS, "lastCheckedAt": self._checked_at, "changeCheckProvider": "ipify", "notice": "Automatic ISP detection reports the current public internet exit network. VPNs, mobile hotspots and wholesale networks can differ from your retail ISP."}

    def detect(self, allow_external=False, *, force_refresh=False, prefer_detected=False):
        settings = self._settings()
        if allow_external is not True and settings.get("allowExternalLookup") is not True:
            raise PermissionError("ISP detection requires explicit consent to contact ipwho.is. You can enter your provider manually instead.")
        overview = self._overview_provider()
        self._refresh_network(overview)
        if not self._lookup_lock.acquire(blocking=False):
            return {**self.snapshot(overview, prefer_detected=prefer_detected), "lookupInProgress": True}
        try:
            now = time.time()
            signature = self._network_signature(overview)
            with self._lock:
                if not force_refresh and self._isp and now - self._isp.get("detectedAt", 0) < CACHE_SECONDS:
                    return self.snapshot(overview, prefer_detected=prefer_detected)
                if not force_refresh and self._last_attempt and now - self._last_attempt < CACHE_SECONDS:
                    return self.snapshot(overview, prefer_detected=prefer_detected)
                self._last_attempt = now
            try:
                # No request body, custom IP, cookies, API key or LAN inventory.
                raw = safe_request(LOOKUP_URL, timeout=5, allow_remote=True, max_bytes=32768, headers={"Accept": "application/json"})
                if len(raw) > 32768:
                    raise ValueError("Oversized ISP response.")
                response = json.loads(raw)
                connection = response.get("connection") if isinstance(response, dict) else None
                if response.get("success") is not True or not isinstance(connection, dict):
                    raise ValueError("ISP lookup failed.")
                reported_name = clean_name(connection.get("isp") or connection.get("org"))
                if not reported_name:
                    raise ValueError("ISP lookup did not identify a network.")
                with self._lock:
                    if signature == self._network_signature(self._overview_provider()):
                        self._isp = {**canonical_name(reported_name), "reportedName": reported_name, "source": "ipwho.is", "detectedAt": time.time(), "status": "detected"}
                        self._error = None
            except Exception:
                with self._lock:
                    self._error = "Provider lookup is unavailable. Enter your ISP manually or try again in a minute."
            return self.snapshot(prefer_detected=prefer_detected)
        finally:
            self._lookup_lock.release()

    def settings_changed(self):
        self._wake.set()

    def refresh_automatic(self):
        """Check for exit changes each minute without exhausting ISP API quotas.

        ipify returns only this request's public IPv4. It remains in transient
        memory, never in snapshots, the encrypted vault, logs, or release files.
        ISP metadata is refreshed on a changed exit and at least every six hours.
        """
        settings = self._settings()
        if settings.get("allowExternalLookup") is not True or settings.get("ispName"):
            return self.snapshot()
        overview = self._overview_provider()
        self._refresh_network(overview)
        if not self._check_lock.acquire(blocking=False):
            return self.snapshot()
        try:
            now = time.time()
            signature = self._network_signature(overview)
            with self._lock:
                if self._last_check_attempt and now - self._last_check_attempt < CACHE_SECONDS:
                    return self.snapshot()
                self._last_check_attempt = now
            try:
                raw = safe_request(CHANGE_CHECK_URL, timeout=5, allow_remote=True, max_bytes=256, headers={"Accept": "application/json"})
                if len(raw) > 256:
                    raise ValueError("Oversized change-check response.")
                payload = json.loads(raw)
                address = ipaddress.ip_address(payload.get("ip", "") if isinstance(payload, dict) else "")
                if address.version != 4 or not address.is_global:
                    raise ValueError("Expected the public IPv4 of this request.")
                with self._lock:
                    if signature != self._network_signature(self._overview_provider()):
                        return self.snapshot()
                    unchanged = self._public_ip == str(address)
                    fresh = self._isp and now - self._isp.get("detectedAt", 0) < PROVIDER_CACHE_SECONDS
                    needs_lookup = not unchanged or not fresh or self._error
                if needs_lookup:
                    result = self.detect(force_refresh=True, prefer_detected=True)
                    if result.get("lookupInProgress") or result["isp"].get("status") != "detected":
                        return self.snapshot()
                with self._lock:
                    if signature == self._network_signature(self._overview_provider()):
                        self._public_ip, self._checked_at, self._error = str(address), time.time(), None
            except Exception:
                with self._lock:
                    self._error = "Automatic provider check is unavailable. The last known provider is retained; retrying in a minute."
            return self.snapshot()
        finally:
            self._check_lock.release()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.refresh_automatic()
            except PermissionError:
                pass
            with self._lock:
                wait_seconds = min(CACHE_SECONDS, max(.25, self._last_check_attempt + CACHE_SECONDS - time.time())) if self._last_check_attempt else CACHE_SECONDS
            self._wake.wait(wait_seconds)
            self._wake.clear()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="opt-in-network-identity", daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2)
