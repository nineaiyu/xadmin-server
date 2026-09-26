# -*- coding: utf-8 -*-
"""common/core/permission_meta.py 单元测试（子 action 权限口径注册表）。

覆盖：两档语义登记、正则编译与缓存、装饰器默认命名（与 DRF 同规则）、
二开扩展场景（自注册后缀不改权限核心类）。
"""

import pytest

from common.core import permission_meta


@pytest.fixture(autouse=True)
def _isolate_registry():
    """注册表是「导入期状态」：装饰器随视图模块导入执行一次，清空后不可通过
    重新触发 URLconf 恢复（模块已缓存于 sys.modules）。因此用例隔离只移除
    本用例新增的后缀，绝不整体清空。"""
    before_shared = set(permission_meta.SHARED_LIST_SUFFIXES)
    before_fallback = set(permission_meta.PARENT_FALLBACK_SUFFIXES)
    yield
    permission_meta.SHARED_LIST_SUFFIXES -= before_shared
    permission_meta.SHARED_LIST_SUFFIXES |= before_shared
    permission_meta.PARENT_FALLBACK_SUFFIXES -= before_fallback
    permission_meta.PARENT_FALLBACK_SUFFIXES |= before_fallback


def test_framework_default_suffixes_registered():
    """框架默认口径随 URLconf 装载注册（demo 视图/表单/联想/导入导出）。"""
    from django.urls import get_resolver

    _ = get_resolver().url_patterns  # 触发视图模块导入 → 装饰器执行
    assert {"search-columns", "suggestions", "available-forms", "user-options"} <= permission_meta.SHARED_LIST_SUFFIXES
    assert {"export-data", "export-async", "import-data", "import-headers", "import-validate", "import-async"} <= (
        permission_meta.PARENT_FALLBACK_SUFFIXES
    )


def test_shared_list_pattern_strips_suffix_once():
    permission_meta.register_shared_list("search-columns")
    pattern = permission_meta.shared_list_pattern()
    assert pattern.sub("", "/api/demo/book/search-columns", count=1) == "/api/demo/book"
    # 不以后缀结尾的 URL 不剥（与历史 $ 锚定语义一致）
    assert pattern.sub("", "/api/demo/book/search-columns/1", count=1) == "/api/demo/book/search-columns/1"
    assert pattern.sub("", "/api/demo/book", count=1) == "/api/demo/book"


def test_parent_fallback_pattern():
    permission_meta.register_parent_fallback("import-data")
    pattern = permission_meta.parent_fallback_pattern()
    m = pattern.search("/api/demo/book/import-data")
    assert m
    assert "/api/demo/book/import-data"[: m.start()] == "/api/demo/book"


def test_decorator_registers_and_still_returns_action():
    """装饰器登记后仍返回 DRF action 包装结果（url_path 缺省时按方法名派生）。"""
    decorator = permission_meta.shared_list_action(methods=["get"], detail=False)

    @decorator
    def my_custom_action(self, request):
        return None

    assert "my-custom-action" in permission_meta.SHARED_LIST_SUFFIXES  # DRF 同规则：下划线转连字符
    assert getattr(my_custom_action, "mapping", None) is not None  # DRF action 包装结果（映射方法表）


def test_custom_suffix_extends_without_touching_core():
    """二开场景：新子 action 自行登记后，解析口径立即生效——无需改权限核心类。"""
    permission_meta.register_shared_list("region-stats")
    pattern = permission_meta.shared_list_pattern()
    assert pattern.sub("", "/api/crm/customer/region-stats", count=1) == "/api/crm/customer"
