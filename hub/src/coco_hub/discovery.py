"""Bonjour advertisement of the Hub as ``_coco._tcp``."""

import ipaddress
import logging
import socket

import ifaddr
from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

from coco_hub.identity import HubIdentity
from coco_hub.settings import Settings

SERVICE_TYPE = "_coco._tcp.local."
log = logging.getLogger(__name__)


def lan_addresses(settings: Settings) -> list[str]:
    """Addresses to advertise: the bound host, or every non-loopback IPv4 if wildcard."""
    if settings.host not in ("0.0.0.0", "", "::"):
        return [settings.host]
    found: list[str] = []
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            if not isinstance(ip.ip, str):  # IPv6 entries are tuples
                continue
            addr = ipaddress.IPv4Address(ip.ip)
            if not (addr.is_loopback or addr.is_link_local) and ip.ip not in found:
                found.append(ip.ip)
    return found


def txt_properties(settings: Settings, ident: HubIdentity) -> dict[str, str]:
    """``api``, ``id`` (Hub ID) and, with TLS, ``fp`` (short certificate fingerprint).

    Advisory only: anyone on the LAN can advertise ``_coco._tcp`` with any TXT record. The
    Display trusts the certificate it pinned when pairing, never these values.
    """
    properties = {"api": "v1", "id": ident.hub_id}
    if settings.tls.enabled:
        properties["fp"] = ident.short_fingerprint
    return properties


def build_service_info(
    settings: Settings, addresses: list[str], ident: HubIdentity
) -> AsyncServiceInfo:
    hostname = socket.gethostname().removesuffix(".local").rstrip(".")
    return AsyncServiceInfo(
        SERVICE_TYPE,
        f"{settings.discovery.name}.{SERVICE_TYPE}",
        port=settings.port,
        parsed_addresses=addresses,
        properties=txt_properties(settings, ident),
        server=f"{hostname}.local.",
    )


class Advertiser:
    def __init__(self, settings: Settings, ident: HubIdentity) -> None:
        self._settings = settings
        self._identity = ident
        self._zc: AsyncZeroconf | None = None
        self._info: AsyncServiceInfo | None = None

    async def start(self) -> None:
        addresses = lan_addresses(self._settings)
        self._info = build_service_info(self._settings, addresses, self._identity)
        self._zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
        await self._zc.async_register_service(self._info, allow_name_change=True)
        log.info("Advertising %s on %s:%s", self._info.name, addresses, self._settings.port)

    async def stop(self) -> None:
        if self._zc is None:
            return
        if self._info is not None:
            await self._zc.async_unregister_service(self._info)
        await self._zc.async_close()
        self._zc = None
