# -*- coding: utf-8 -*-
"""LDAP StartTLS 必须在 bind 之前，避免口令明文出网。"""

from types import SimpleNamespace

from identity.ldap import client


class _FakeConnection:
    calls: list[str] = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        type(self).calls = []

    def start_tls(self):
        type(self).calls.append("start_tls")

    def bind(self):
        type(self).calls.append("bind")


def _config(start_tls: bool):
    return SimpleNamespace(
        start_tls=start_tls,
        bind_dn="cn=service,dc=example,dc=com",
        bind_password="service-secret",
    )


def test_service_connection_starts_tls_before_bind(monkeypatch):
    monkeypatch.setattr(client, "build_server", lambda config: object())
    monkeypatch.setattr(client, "Connection", _FakeConnection)

    client.service_connection(_config(True))

    assert _FakeConnection.calls == ["start_tls", "bind"]


def test_user_connection_starts_tls_before_bind(monkeypatch):
    monkeypatch.setattr(client, "build_server", lambda config: object())
    monkeypatch.setattr(client, "Connection", _FakeConnection)

    client.user_connection("cn=alice,dc=example,dc=com", "secret", _config(True))

    assert _FakeConnection.calls == ["start_tls", "bind"]


def test_plain_ldap_still_binds(monkeypatch):
    monkeypatch.setattr(client, "build_server", lambda config: object())
    monkeypatch.setattr(client, "Connection", _FakeConnection)

    client.service_connection(_config(False))

    assert _FakeConnection.calls == ["bind"]
