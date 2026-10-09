# -*- coding: utf-8 -*-
"""URL 路由完整性守护：静态路由不得被更早注册的动态路由遮蔽。

背景（真实缺陷）：identity 与 audit 同挂 ``/api/system/`` 前缀时，identity 的
``user/(?P<pk>[^/.]+)`` detail 路由注册在前、audit 的静态 ``user/log``（个人安全
日志）注册在后——``/api/system/user/log`` 永远命中前者（pk 被当成 "log" 解析），
安全日志接口实际不可达；页面静默空列表，无任何测试察觉。四域改独立前缀后该冲突
消除；本测试把「静态路由被遮蔽」固化为 CI 门禁，防止后续新增域/路由时复现同类
静默失效。

检测口径：遍历 URLConf 收集「静态样板路径」（不含分组/转换器的 pattern），按注册
顺序检查其是否被更早的任意路由（含动态路由）匹配——命中即视为被遮蔽。
工具实现见 ``tests/url_routes.py``。
"""

from types import SimpleNamespace

from django.http import HttpResponse
from django.urls import get_resolver, re_path

from tests.url_routes import iter_leaves, scan_shadowed, static_sample


class TestRouteShadowingDetector:
    """检测器负向自测：构造「静态路由被前置动态路由遮蔽」时必须能发现。"""

    def test_detects_static_route_shadowed_by_dynamic_detail(self):
        def _view_a(request):
            return HttpResponse("a")

        def _view_b(request):
            return HttpResponse("b")

        # 复刻历史结构：先注册 detail（user/<pk>），再注册静态 user/log
        resolver = SimpleNamespace(
            url_patterns=[
                re_path(r"^user/(?P<pk>[^/.]+)$", _view_a, name="user-detail"),
                re_path(r"^user/log$", _view_b, name="user-log"),
            ]
        )
        shadowed = scan_shadowed(iter_leaves(resolver))
        assert [url for url, _ in shadowed] == ["user/log"]

    def test_no_false_positive_when_detail_registered_after(self):
        """静态路由注册在前时无冲突（顺序正确即通过）。"""

        def _view_a(request):
            return HttpResponse("a")

        def _view_b(request):
            return HttpResponse("b")

        resolver = SimpleNamespace(
            url_patterns=[
                re_path(r"^user/log$", _view_a, name="user-log"),
                re_path(r"^user/(?P<pk>[^/.]+)$", _view_b, name="user-detail"),
            ]
        )
        assert scan_shadowed(iter_leaves(resolver)) == []


class TestFullUrlConfIntegrity:
    def test_no_static_route_is_shadowed(self):
        """真实 URLConf 全量扫描：任何静态路由都必须能命中自身。"""
        shadowed = scan_shadowed(iter_leaves(get_resolver()))
        detail = "\n".join(f"  {url} 被 {by} 遮蔽" for url, by in shadowed)
        assert shadowed == [], f"以下静态路由被更早注册的路由遮蔽（永远不可达）：\n{detail}"

    def test_scan_covers_expected_scale(self):
        """扫描面非空（防 URLConf 未加载导致空集假绿）。"""
        leaves = list(iter_leaves(get_resolver()))
        assert len(leaves) > 200, f"路由遍历结果异常（{len(leaves)} 条），检查 URLconf 加载"

    def test_domain_prefixes_registered(self):
        """四域独立前缀可达性（路由面）：各域代表性端点存在于扫描面。"""
        statics = {
            prefix + sample for prefix, leaf in iter_leaves(get_resolver()) if (sample := static_sample(leaf.pattern))
        }
        assert "api/identity/login/basic" in statics
        assert "api/identity/userinfo" in statics
        assert "api/audit/user/log" in statics
        assert "api/audit/logs/operation" in statics
        assert "api/task/exports" in statics
        assert "api/file/file" in statics
