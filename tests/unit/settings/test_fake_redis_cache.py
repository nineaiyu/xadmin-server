"""E2E 档进程内缓存后端的兼容能力守护。

``FakeRedisCache`` 只在 ``tests.settings_e2e`` 下生效（E2E 真库档），但它的能力
缺口只在 E2E 运行时暴露——此前缺 ``iter_keys`` 导致安全拦截 IP 列表接口 500、
页面降级为空表；缺 ``ttl`` 导致验证码重复发送的限流闸门在构造业务异常处抛
AttributeError（E2E 档表现为未预期 500）。此处在单测中直接实例化后端，守护
django-redis 特有方法的语义与生产口径一致（返回逻辑键、惰性迭代、剩余秒、
扩展 kwargs 兼容）。
"""

import re

import pytest

from tests.cache_backend import FakeRedisCache


@pytest.fixture()
def fake_cache():
    return FakeRedisCache("iter-keys-test", {})


def test_iter_keys_returns_logical_keys_by_pattern(fake_cache):
    """返回不含 prefix/version 的逻辑键（与生产 reverse_key 口径一致）。"""
    fake_cache.set("_LOGIN_BLOCK_IP_1.2.3.4", "1")
    fake_cache.set("_LOGIN_BLOCK_IP_5.6.7.8", "1")
    fake_cache.set("unrelated", "1")
    keys = list(fake_cache.iter_keys("_LOGIN_BLOCK_IP_*"))
    assert sorted(keys) == ["_LOGIN_BLOCK_IP_1.2.3.4", "_LOGIN_BLOCK_IP_5.6.7.8"]


def test_iter_keys_is_lazy_iterator_and_matches_keys(fake_cache):
    """生产走 Redis SCAN 游标（惰性）；与 keys() 的命中集合必须一致。"""
    fake_cache.set("a-1", "1")
    fake_cache.set("a-2", "1")
    it = fake_cache.iter_keys("a-*")
    assert iter(it) is it
    assert sorted(it) == sorted(fake_cache.keys("a-*"))


def test_iter_keys_skips_expired_and_accepts_extra_kwargs(fake_cache):
    """生产签名带 iter_keys_timeout 等扩展参数——调用方透传 kwargs 不得报错。"""
    fake_cache.set("expired-key", "1", timeout=0)
    fake_cache.set("alive-key", "1")
    keys = list(fake_cache.iter_keys("*-key", iter_keys_timeout=5))
    assert keys == ["alive-key"]


def test_delete_pattern_removes_matching_entries(fake_cache):
    """通配删除走内部 _delete（同时清 _cache 与 _expire_info），返回真实删除数。"""
    fake_cache.set("dept_recursion_1", "1")
    fake_cache.set("dept_recursion_2", "1")
    fake_cache.set("keep", "1")
    assert fake_cache.delete_pattern("dept_recursion_*") == 2
    assert fake_cache.get("keep") == "1"


def test_ttl_matches_production_semantics(fake_cache):
    """生产 ``DefaultClient.ttl`` 口径：不存在返回 0 / 无过期返回 None / 其余为剩余秒。"""
    assert fake_cache.ttl("ttl-probe-missing") == 0

    fake_cache.set("ttl-probe-60s", "1", timeout=60)
    remaining = fake_cache.ttl("ttl-probe-60s")
    assert isinstance(remaining, int)
    assert 0 < remaining <= 60

    fake_cache.set("ttl-probe-forever", "1", timeout=None)
    assert fake_cache.ttl("ttl-probe-forever") is None

    fake_cache.set("ttl-probe-expired", "1", timeout=0)
    assert fake_cache.ttl("ttl-probe-expired") == 0


def test_resend_rate_limit_returns_readable_seconds(fake_cache, monkeypatch):
    """重复发码的限流闸门经 ``cache.ttl`` 产出可读秒数，而非 AttributeError。

    链路：``SendAndVerifyCodeUtil.gen_and_send_async`` → ``__rata``（限流窗口内
    已有发送记录）→ ``cache.ttl(limit_key)``；缺 ttl 时该分支在构造
    ``CodeSendOverRate`` 的入参求值处就抛异常（E2E 档表现为未预期 500）。
    """
    from common.utils.verify_code import CodeSendOverRate, SendAndVerifyCodeUtil

    monkeypatch.setattr("common.utils.verify_code.cache", fake_cache)
    util = SendAndVerifyCodeUtil("ttl-probe-user@example.com", backend="email")
    fake_cache.set(util.limit_key, "1", 60)
    with pytest.raises(CodeSendOverRate) as exc:
        util.gen_and_send_async()
    seconds = re.search(r"\d+", str(exc.value.detail))
    assert seconds, f"限流提示应包含剩余秒数：{exc.value.detail}"
    assert 0 < int(seconds.group()) <= 60
