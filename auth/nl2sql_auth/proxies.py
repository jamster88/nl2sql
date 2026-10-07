"""Whose word to take for where a sign-in came from (V6-63).

The per-address throttle counts wrong passwords by the address a sign-in
came from. Behind one of this stack's page proxies that is the last hop of
`X-Forwarded-For`, the one the proxy appended; but this service is also
published on its own port for the desktop client, and a caller there has no
proxy in front -- the whole header is theirs, and a new address in it each
time made the per-address limit no limit at all.

So the header is read only from a caller this service trusts to have written
it: `AUTH_TRUSTED_PROXIES`, addresses, networks or names. A name is looked
up, and again every half minute, because a container's address is whatever
Docker gave it this time; compose names the six page proxies by their network
aliases. Anyone else is counted by the address the connection came from.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from typing import Callable, Iterable

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network

#: How long a name's addresses are believed.
RESOLVE_SECONDS = 30.0


def _address(text: str) -> Address | None:
    """An address, as a peer or a header gives it: IPv4-mapped IPv6 as IPv4,
    a zone dropped. None for anything that is not an address."""
    try:
        address = ipaddress.ip_address(text.strip().split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


class TrustedProxies:
    """The callers whose `X-Forwarded-For` is believed."""

    def __init__(
        self,
        entries: Iterable[str] = (),
        *,
        resolve: Callable = socket.getaddrinfo,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.networks: list[Network] = []
        self.names: list[str] = []
        for entry in (entry.strip() for entry in entries):
            if not entry:
                continue
            try:
                self.networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                self.names.append(entry)
        self._resolve = resolve
        self._clock = clock
        self._resolved: dict[str, tuple[frozenset[Address], float]] = {}
        self._lock = threading.Lock()

    def __bool__(self) -> bool:
        return bool(self.networks or self.names)

    def _addresses_of(self, name: str) -> frozenset[Address]:
        now = self._clock()
        with self._lock:
            cached = self._resolved.get(name)
        if cached is not None and now - cached[1] < RESOLVE_SECONDS:
            return cached[0]
        try:
            found = frozenset(
                address for *_, sockaddr in self._resolve(name, None) if (address := _address(sockaddr[0])) is not None
            )
        except OSError:
            # Not running -- MLflow's proxy is in a profile of its own -- so
            # nothing can arrive from it.
            found = frozenset()
        with self._lock:
            self._resolved[name] = (found, now)
        return found

    def trusts(self, peer: str) -> bool:
        address = _address(peer)
        if address is None:
            return False
        if any(address in network for network in self.networks):
            return True
        return any(address in self._addresses_of(name) for name in self.names)

    def client(self, peer: str | None, forwarded: str) -> str:
        """The address a request came from: the last hop a trusted proxy
        recorded, else the connection's own.

        The last hop, not the first: the proxy appends what it saw, and
        anything before it is whatever the client claimed.
        """
        if peer is None:
            return "unknown"
        if not self.trusts(peer):
            return peer
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        return hops[-1] if hops else peer
