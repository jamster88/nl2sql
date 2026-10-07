"""Whose word to take for where a sign-in came from (V6-63)."""

from __future__ import annotations

import socket

import pytest

from nl2sql_auth import proxies as proxies_module
from nl2sql_auth.proxies import TrustedProxies


class Resolver:
    """getaddrinfo, from a table, counting the questions."""

    def __init__(self, table: dict[str, list[str]]) -> None:
        self.table = table
        self.asked: list[str] = []

    def __call__(self, name, port):
        self.asked.append(name)
        if name not in self.table:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0)) for address in self.table[name]]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_nobody_is_trusted_by_default():
    proxies = TrustedProxies()
    assert not proxies
    assert proxies.client("10.0.0.1", "203.0.113.9") == "10.0.0.1"


@pytest.mark.parametrize(
    ("entry", "peer", "trusted"),
    [
        ("10.0.0.7", "10.0.0.7", True),
        ("10.0.0.7", "10.0.0.8", False),
        ("172.18.0.0/16", "172.18.4.2", True),
        ("172.18.0.0/16", "172.19.0.1", False),
        ("fd00::/8", "fd12::1", True),
        ("10.0.0.7", "::ffff:10.0.0.7", True),
        ("fe80::1", "fe80::1%eth0", True),
        ("10.0.0.7", "testclient", False),
    ],
)
def test_an_address_or_a_network(entry, peer, trusted):
    assert TrustedProxies([entry]).trusts(peer) is trusted


def test_a_name_is_looked_up_and_believed_for_half_a_minute():
    resolver, clock = Resolver({"nl2sql-gui": ["172.18.0.5"]}), Clock()
    proxies = TrustedProxies([" nl2sql-gui ", ""], resolve=resolver, clock=clock)
    assert proxies.names == ["nl2sql-gui"] and proxies.networks == []
    assert proxies.trusts("172.18.0.5") and not proxies.trusts("172.18.0.6")
    assert resolver.asked == ["nl2sql-gui"]
    resolver.table["nl2sql-gui"] = ["172.18.0.9"]  # restarted, with a new address
    clock.now += proxies_module.RESOLVE_SECONDS
    assert proxies.trusts("172.18.0.9") and not proxies.trusts("172.18.0.5")
    assert resolver.asked == ["nl2sql-gui", "nl2sql-gui"]


def test_a_name_that_does_not_resolve_trusts_nothing_until_it_does():
    resolver, clock = Resolver({}), Clock()
    proxies = TrustedProxies(["nl2sql-mlflow-proxy"], resolve=resolver, clock=clock)
    assert not proxies.trusts("172.18.0.5")
    resolver.table["nl2sql-mlflow-proxy"] = ["172.18.0.5"]
    assert not proxies.trusts("172.18.0.5"), "the failure is remembered too"
    clock.now += proxies_module.RESOLVE_SECONDS
    assert proxies.trusts("172.18.0.5")


def test_an_answer_that_is_not_an_address_is_left_out():
    resolver = Resolver({"odd": ["not-an-address", "10.1.1.1"]})
    proxies = TrustedProxies(["odd"], resolve=resolver)
    assert proxies.trusts("10.1.1.1")


def test_a_trusted_proxy_is_believed_for_the_last_hop_only():
    proxies = TrustedProxies(["10.0.0.0/8"])
    assert proxies.client("10.0.0.2", "6.6.6.6, 203.0.113.9") == "203.0.113.9"
    assert proxies.client("10.0.0.2", " , ") == "10.0.0.2", "nothing forwarded: the proxy itself"
    assert proxies.client("192.0.2.1", "203.0.113.9") == "192.0.2.1", "anyone else: their own address"
    assert proxies.client(None, "203.0.113.9") == "unknown"
