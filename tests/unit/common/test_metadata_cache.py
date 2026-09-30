# -*- coding: utf-8 -*-
"""元数据载荷缓存。

search-columns / search-fields 每次都由视图集现场重建 filterset + 逐字段求值，
前端每个列表页都会取（首开还经 with_meta=1 内联进列表响应）。缓存键必须含用户
（字段权限按用户不同）与 ?fields=（收窄序列化器字段），且缓存命中时响应仍是普通
ApiResponse —— 内联路径依赖 result.data。
"""

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

import common.core.modelset.metadata as metadata_module

pytestmark = pytest.mark.django_db

COLUMNS_URL = "/api/demo/book/search-columns"
FIELDS_URL = "/api/demo/book/search-fields"


def _spy_build(monkeypatch):
    """记录元数据「逐字段求值」真实发生的次数（命中缓存时不应再执行）。"""
    calls = []
    original = metadata_module.get_format_intput_type

    def spy(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(metadata_module, "get_format_intput_type", spy)
    return calls


def test_second_request_served_from_cache(superuser, monkeypatch):
    client = APIClient()
    client.force_authenticate(superuser)

    calls = _spy_build(monkeypatch)
    first = client.get(COLUMNS_URL)
    assert first.status_code == 200
    assert first.data["code"] == 1000
    built = len(calls)
    assert built > 0

    second = client.get(COLUMNS_URL)
    assert second.status_code == 200
    assert second.data["data"] == first.data["data"]
    # 命中缓存：不再逐字段求值
    assert len(calls) == built


def test_search_fields_cached(superuser, monkeypatch):
    client = APIClient()
    client.force_authenticate(superuser)

    calls = _spy_build(monkeypatch)
    first = client.get(FIELDS_URL)
    assert first.data["code"] == 1000
    built = len(calls)

    second = client.get(FIELDS_URL)
    assert second.data["data"] == first.data["data"]
    assert len(calls) == built


def test_no_cache_query_bypasses(superuser, monkeypatch):
    client = APIClient()
    client.force_authenticate(superuser)

    calls = _spy_build(monkeypatch)
    assert client.get(COLUMNS_URL).data["code"] == 1000
    built = len(calls)

    # 旁路读：重新求值；且不回写缓存
    assert client.get(f"{COLUMNS_URL}?no_cache=1").data["code"] == 1000
    assert len(calls) == built * 2


def test_cache_key_is_user_scoped(superuser):
    """键必须区分用户：字段权限按用户不同，共用键会串号。"""
    from django.test import RequestFactory

    from common.core.modelset.metadata import metadata_cache_key

    class BookViewSet:
        pass

    view = BookViewSet()

    def _request(user):
        request = RequestFactory().get(COLUMNS_URL)
        request.user = user
        return request

    other = type("U", (), {"pk": superuser.pk + 1})()
    assert metadata_cache_key(view, "search_columns", _request(superuser)) != metadata_cache_key(
        view, "search_columns", _request(other)
    )


def test_fields_param_has_separate_key(superuser, monkeypatch):
    client = APIClient()
    client.force_authenticate(superuser)

    calls = _spy_build(monkeypatch)
    client.get(COLUMNS_URL)
    built = len(calls)
    # ?fields= 收窄序列化器字段：必须独立建键（否则受限响应污染默认键）
    client.get(f"{COLUMNS_URL}?fields=name")
    assert len(calls) > built


def test_with_meta_inline_reuses_metadata_cache(superuser, monkeypatch):
    """列表 with_meta=1 内联元数据：命中缓存时仍能拿到 data 载荷（响应对象语义不变）。"""
    client = APIClient()
    client.force_authenticate(superuser)

    # 先填充两个载荷缓存（独立元数据接口）
    assert client.get(COLUMNS_URL).data["code"] == 1000
    assert client.get(FIELDS_URL).data["code"] == 1000

    calls = _spy_build(monkeypatch)
    response = client.get("/api/demo/book?with_meta=1&page=1&size=10")
    assert response.status_code == 200
    payload = response.data["data"]
    assert isinstance(payload.get("search_columns"), list) and payload["search_columns"]
    assert "search_fields" in payload
    # 内联路径同样命中缓存（未重新逐字段求值）
    assert calls == []


def test_cache_entry_written_with_ttl(superuser, monkeypatch):
    from common.core.modelset.metadata import METADATA_CACHE_TIMEOUT

    writes = []
    original_set = cache.set
    monkeypatch.setattr(
        "django.core.cache.cache.set",
        lambda key, value, timeout=None, **kwargs: (
            writes.append((key, timeout)),
            original_set(key, value, timeout, **kwargs),
        )[1],
    )

    client = APIClient()
    client.force_authenticate(superuser)
    assert client.get(COLUMNS_URL).data["code"] == 1000

    assert METADATA_CACHE_TIMEOUT == 60 * 5
    assert any(
        key.startswith("metadata_payload_BookViewSet_search_columns") and timeout == METADATA_CACHE_TIMEOUT
        for key, timeout in writes
    )


NOTIFY_IM_URL = "/api/settings/notify/im/search-columns"


def test_extra_cache_key_hook_separates_channel_scoped_metadata(superuser, monkeypatch):
    """序列化器字段面依赖 ?channel=（get_serializer_class 收敛）的视图集：

    metadata_extra_cache_key 必须并入缓存键——否则首个渠道的元数据被跨渠道
    复用（回归：设置页飞书/钉钉页签互相拿到对方的字段面，飞书页签渲染空白）。
    """
    client = APIClient()
    client.force_authenticate(superuser)

    calls = _spy_build(monkeypatch)
    feishu = client.get(f"{NOTIFY_IM_URL}?channel=feishu")
    assert feishu.status_code == 200
    keys_feishu = [col["key"] for col in feishu.data["data"]]
    assert keys_feishu and all(key.startswith("FEISHU") for key in keys_feishu)

    # 换渠道：不得命中飞书的缓存（修复前返回飞书字段面），须真实重建
    dingtalk = client.get(f"{NOTIFY_IM_URL}?channel=dingtalk")
    keys_dingtalk = [col["key"] for col in dingtalk.data["data"]]
    assert keys_dingtalk and all(key.startswith("DINGTALK") for key in keys_dingtalk)
    assert len(calls) > 0

    # 同渠道再次请求：仍命中缓存
    built = len(calls)
    again = client.get(f"{NOTIFY_IM_URL}?channel=dingtalk")
    assert [col["key"] for col in again.data["data"]] == keys_dingtalk
    assert len(calls) == built


def test_tag_creation_invalidates_embedded_options(superuser):
    """嵌入元数据的引用数据（标签选项）变更须整族失效载荷缓存。

    回归（e2e 并行实测）：先预热用户列表元数据（选项不含新标签）→ ORM 新建
    标签（内置同步等 ORM 直改路径同样要触发，故失效挂模型信号）→ 再取元数据
    必须看到新标签，而非命中 TTL 内的旧载荷。
    """
    from system.models.tag import Tag

    client = APIClient()
    client.force_authenticate(superuser)

    def _tag_options():
        data = client.get("/api/system/user/search-fields").data["data"]
        tag_col = next(col for col in data if col["key"] == "tag")
        return [item["value"] for item in (tag_col.get("choices") or [])]

    assert "回归标签e2e" not in _tag_options()
    Tag.objects.create(name="回归标签e2e")
    assert "回归标签e2e" in _tag_options()


class _FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _RecordingCache:
    """可编排的缓存替身：按序返回 get 结果、记录 set 与锁获取。"""

    def __init__(self, gets=None, lock_error=None):
        self._gets = list(gets or [])
        self.get_calls = 0
        self.set_calls = []
        self.locked = 0
        self._lock_error = lock_error

    def get(self, key):
        self.get_calls += 1
        if self._gets:
            return self._gets.pop(0)
        return None

    def set(self, key, value, timeout):
        self.set_calls.append((key, value, timeout))

    def lock(self, *args, **kwargs):
        if self._lock_error:
            raise self._lock_error
        self.locked += 1
        return _FakeLock()


class TestSingleFlight:
    """单飞重建（与 MagicCacheResponse 同范式）：冷缓存击穿只回源一次。"""

    def test_lock_second_check_reuses_winner(self, monkeypatch):
        """进入锁前未命中、锁内命中（并发对手已完成重建）时不再重建、不回写。"""
        from common.core.modelset import metadata_cache

        fake = _RecordingCache(gets=[None, ["cached"]])
        monkeypatch.setattr(metadata_cache, "cache", fake)
        built = []

        def builder():
            built.append(1)
            return ["built"]

        result = metadata_cache.cached_payload("k", 60, builder)
        assert result == ["cached"]
        assert built == []
        assert fake.locked == 1
        assert fake.set_calls == []

    def test_miss_builds_and_writes_once(self, monkeypatch):
        from common.core.modelset import metadata_cache

        fake = _RecordingCache()
        monkeypatch.setattr(metadata_cache, "cache", fake)
        result = metadata_cache.cached_payload("k", 60, lambda: ["built"])
        assert result == ["built"]
        assert fake.set_calls == [("k", ["built"], 60)]

    def test_lock_timeout_falls_back_to_direct_build(self, monkeypatch):
        """等锁超时（生产 redis LockError）：降级直接重建，不把争用升级为错误。"""
        from redis.exceptions import LockError

        from common.core.modelset import metadata_cache

        fake = _RecordingCache(lock_error=LockError("timeout"))
        monkeypatch.setattr(metadata_cache, "cache", fake)
        assert metadata_cache.cached_payload("k", 60, lambda: ["direct"]) == ["direct"]
        assert fake.set_calls == []

    def test_none_build_result_not_cached(self, monkeypatch):
        """构建失败（None）不写缓存——避免把失败态缓存成「成功但残缺」。"""
        from common.core.modelset import metadata_cache

        fake = _RecordingCache()
        monkeypatch.setattr(metadata_cache, "cache", fake)
        assert metadata_cache.cached_payload("k", 60, lambda: None) is None
        assert fake.set_calls == []

    def test_bypass_skips_cache(self, monkeypatch):
        from common.core.modelset import metadata_cache

        fake = _RecordingCache(gets=[["stale"]])
        monkeypatch.setattr(metadata_cache, "cache", fake)
        assert metadata_cache.cached_payload("k", 60, lambda: ["fresh"], bypass=True) == ["fresh"]
        assert fake.get_calls == 0
        assert fake.set_calls == []
