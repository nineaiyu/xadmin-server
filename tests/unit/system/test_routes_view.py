# -*- coding: utf-8 -*-
"""MagicCacheResponse（cache_response 装饰器）测试。

路由/面板缓存共用这套基建，覆盖：
1. 首次执行视图并缓存，二次命中缓存（不再产生业务 SQL）；
2. 缓存 key 按用户隔离；
3. 4xx / request.no_cache 不写缓存；
4. invalid_cache 失效后重新计算；
5. 路由输出包含 meta 信息（PERF-17 的 select_related 不改变输出）。
"""
import json

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from common.base.magic import cache_response
from system.models import Menu, MenuMeta

pytestmark = pytest.mark.django_db

ROUTES_URL = "/api/system/routes"


def payload(response):
    """统一从已渲染内容解析：首刷响应中的 UUID 在 JSON 渲染后才变为字符串，
    直接读 .data 会得到 UUID 对象，与缓存命中后的字符串形式不可比。"""
    return json.loads(response.content.decode())


def _business_queries(ctx):
    return [q for q in ctx.captured_queries if "SAVEPOINT" not in q["sql"]]


@pytest.fixture(autouse=True)
def _menu_tree(db):
    """两级菜单树，parent/child 均带 meta"""
    parent_meta = MenuMeta.objects.create(title="系统管理")
    parent = Menu.objects.create(name="系统管理", path="system", menu_type=Menu.MenuChoices.DIRECTORY,
                                 meta=parent_meta)
    for i, name in enumerate(["用户管理", "角色管理"]):
        meta = MenuMeta.objects.create(title=name)
        Menu.objects.create(name=name, path=f"api/system/{'user' if i == 0 else 'role'}$",
                            method="GET", menu_type=Menu.MenuChoices.MENU, parent=parent, meta=meta)
    return parent


class TestRoutesResponseCache:
    def test_second_call_served_from_cache(self, auth_client):
        with CaptureQueriesContext(connection) as first_ctx:
            first = auth_client.get(ROUTES_URL)
        with CaptureQueriesContext(connection) as second_ctx:
            second = auth_client.get(ROUTES_URL)

        assert first.status_code == 200 and second.status_code == 200
        assert payload(second)["data"] == payload(first)["data"]
        assert payload(second)["auths"] == payload(first)["auths"]
        assert len(_business_queries(first_ctx)) > 0
        assert len(_business_queries(second_ctx)) == 0

    def test_cache_key_is_per_user(self, api_client, superuser, normal_user):
        """缓存 key 含用户维度：不同用户不共享同一份路由"""
        api_client.force_authenticate(user=superuser)
        super_result = payload(api_client.get(ROUTES_URL))
        api_client.force_authenticate(user=normal_user)
        normal_result = payload(api_client.get(ROUTES_URL))

        # 超管能看到全部菜单，普通用户无菜单授权 -> 空树
        assert len(super_result["data"]) == 1
        assert normal_result["data"] == []

    def test_response_contains_menu_meta(self, auth_client):
        """首刷响应包含 meta 信息（PERF-17 的 select_related 不改变输出）"""
        result = payload(auth_client.get(ROUTES_URL))
        children = result["data"][0]["children"]
        assert [child["name"] for child in children] == ["用户管理", "角色管理"]
        for child in children:
            assert child["meta"]["title"] == child["name"]
            assert {"showLink", "keepAlive", "transition"} <= set(child["meta"])


class CachedView(APIView):
    """最小化可缓存视图：计数器暴露视图方法的真实执行次数"""

    renderer_classes = [JSONRenderer]
    calls = 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.headers = {}  # finalize_response 会 pop 'Vary'


def _make_request(superuser):
    request = Request(APIRequestFactory().get("/"))
    request.user = superuser
    return request


class TestMagicCacheResponseUnit:
    def _handler(self, counter, value_key):
        @cache_response(timeout=60, key_func=lambda **kw: value_key)
        def handler(self, request):
            counter["count"] += 1
            return Response({"n": counter["count"]})

        return handler

    def test_cache_hit_avoids_recompute(self, superuser):
        counter = {"count": 0}
        handler = self._handler(counter, "unit-hit")
        view = CachedView()
        request = _make_request(superuser)

        first = handler(view, request)
        second = handler(view, request)

        assert counter["count"] == 1  # 只执行一次
        assert payload(second)["n"] == payload(first)["n"] == 1
        # 命中缓存返回的是已渲染内容（HttpResponse），而非重新渲染的 Response
        assert not hasattr(second, "data")

    def test_no_cache_flag_bypasses_read_and_write(self, superuser):
        counter = {"count": 0}
        handler = self._handler(counter, "unit-no-cache")
        view = CachedView()
        request = _make_request(superuser)
        request.no_cache = True

        first = handler(view, request)
        second = handler(view, request)

        assert counter["count"] == 2
        assert payload(first)["n"] == 1 and payload(second)["n"] == 2
        assert cache.get("magic_cache_response_unit-no-cache") is None

    def test_invalid_cache_forces_recompute(self, superuser):
        counter = {"count": 0}
        handler = self._handler(counter, "unit-invalid")
        view = CachedView()
        request = _make_request(superuser)

        handler(view, request)
        handler(view, request)
        assert counter["count"] == 1  # 命中缓存

        cache_response.invalid_cache("unit-invalid")
        handler(view, request)
        assert counter["count"] == 2  # 失效后重新计算

    def test_error_response_not_cached(self, superuser):
        """4xx 响应不写缓存，修复后重试能拿到新结果"""
        counter = {"count": 0}

        @cache_response(timeout=60, key_func=lambda **kw: "unit-error")
        def handler(self, request):
            counter["count"] += 1
            return Response({"error": True}, status=400)

        view = CachedView()
        request = _make_request(superuser)

        handler(view, request)
        assert counter["count"] == 1
        assert cache.get("magic_cache_response_unit-error") is None
