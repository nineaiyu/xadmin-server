# -*- coding: utf-8 -*-
"""元数据载荷缓存（P1-5）。

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
