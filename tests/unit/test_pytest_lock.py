# -*- coding: utf-8 -*-
"""``tests/pytest_lock.py`` 的守护测试。

锁本身依赖真实 PG；这里用假连接覆盖分支语义：拿不到锁必须退出、连接失败
fail-open 但要告警、释放必须幂等。
"""

import types

import pytest
from _pytest.outcomes import Exit
from django.conf import settings as django_settings

from tests import pytest_lock


class _Cursor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return (self.result,)


class _Conn:
    def __init__(self, result=True):
        self.cursor_obj = _Cursor(result)
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _pg_default(monkeypatch):
    monkeypatch.delenv("XADMIN_ALLOW_CONCURRENT_PYTEST", raising=False)
    monkeypatch.setitem(
        django_settings.DATABASES,
        "default",
        {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": "127.0.0.1",
            "PORT": 55433,
            "USER": "server",
            "PASSWORD": "pgtest",
        },
    )


def test_acquire_skips_when_concurrency_forced(monkeypatch):
    monkeypatch.setenv("XADMIN_ALLOW_CONCURRENT_PYTEST", "1")
    called = []
    monkeypatch.setattr(pytest_lock, "_connect", lambda db: called.append(db))
    config = types.SimpleNamespace()

    pytest_lock.acquire(config)

    assert called == []
    assert not hasattr(config, "_xadmin_pytest_lock_conn")


def test_acquire_skips_non_postgres(monkeypatch):
    monkeypatch.setitem(django_settings.DATABASES, "default", {"ENGINE": "django.db.backends.sqlite3"})
    called = []
    monkeypatch.setattr(pytest_lock, "_connect", lambda db: called.append(db))
    config = types.SimpleNamespace()

    pytest_lock.acquire(config)

    assert called == []
    assert not hasattr(config, "_xadmin_pytest_lock_conn")


def test_acquire_is_noop_when_already_held(monkeypatch):
    conn = _Conn(result=True)
    config = types.SimpleNamespace(_xadmin_pytest_lock_conn=conn)
    called = []
    monkeypatch.setattr(pytest_lock, "_connect", lambda db: called.append(db))

    pytest_lock.acquire(config)

    assert called == []
    assert config._xadmin_pytest_lock_conn is conn


def test_acquire_holds_connection(monkeypatch):
    conn = _Conn(result=True)
    monkeypatch.setattr(pytest_lock, "_connect", lambda db: conn)
    config = types.SimpleNamespace()

    pytest_lock.acquire(config)

    assert config._xadmin_pytest_lock_conn is conn
    assert conn.cursor_obj.calls[0][0] == pytest_lock.LOCK_KEY_SQL
    assert tuple(conn.cursor_obj.calls[0][1]) == pytest_lock.LOCK_KEY


def test_acquire_exits_when_lock_busy(monkeypatch):
    conn = _Conn(result=False)
    monkeypatch.setattr(pytest_lock, "_connect", lambda db: conn)
    config = types.SimpleNamespace()

    with pytest.raises(Exit) as exc:
        pytest_lock.acquire(config)

    assert conn.closed
    assert "XADMIN_ALLOW_CONCURRENT_PYTEST=1" in str(exc.value)


def test_acquire_warns_and_continues_on_connect_error(monkeypatch):
    def _boom(db):
        raise OSError("boom")

    monkeypatch.setattr(pytest_lock, "_connect", _boom)
    config = types.SimpleNamespace()

    with pytest.warns(RuntimeWarning, match="pytest 并发锁不可用"):
        pytest_lock.acquire(config)

    assert not hasattr(config, "_xadmin_pytest_lock_conn")


def test_release_unlocks_and_closes():
    conn = _Conn(result=True)
    config = types.SimpleNamespace(_xadmin_pytest_lock_conn=conn)

    pytest_lock.release(config)

    assert conn.closed
    assert conn.cursor_obj.calls[0][0] == pytest_lock.UNLOCK_KEY_SQL
    assert config._xadmin_pytest_lock_conn is None


def test_release_is_noop_without_lock():
    config = types.SimpleNamespace()

    pytest_lock.release(config)

    assert not hasattr(config, "_xadmin_pytest_lock_conn")
