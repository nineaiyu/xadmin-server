# -*- coding: utf-8 -*-
"""列表 ViewSet 默认排序门禁：防止新增无排序 ViewSet 触发分页告警与不稳定分页。

背景：DRF 分页器对「既无 queryset.order_by() 又无模型 ``Meta.ordering``」的 queryset
会抛 ``UnorderedObjectListWarning``，且跨页结果可能重复/丢失。判定口径与实现一致：
类上声明 ``ordering``，或模型 ``Meta.ordering`` 非空（``ordering_fields`` 只放开
``?ordering=`` 参数，不提供默认排序）。

与前端 ``elementPlus.spec.ts``（扫描 src 中 el-* 用法比对注册表）同思路：
扫描 + 显式例外清单，而不是只写在文档里靠人记。
"""

import ast
import importlib
import pathlib

import pytest

pytestmark = pytest.mark.django_db

# 具备列表能力的 mixin：类出现在这些基类里即视为「提供列表接口」
LIST_MIXINS = {
    "ListAction",
    "ListDeleteModelSet",
    "BaseModelSet",
    "OnlyListModelSet",
    "BaseViewSet",
    "RecycleBinAction",
}

# 框架抽象基类目录：本身不绑定模型，排序由子类声明
FRAMEWORK_DIR = "common/core/modelset"
# demo app 按项目决策不再维护（演示模型不影响正式项目），不纳入门禁
SKIP_DIRS = ("migrations/", ".venv", "__pycache__", "tests/", "demo/")

# 显式例外（键 = 类名，值 = 原因）；新增例外必须在 PR 里说明理由
EXEMPT = {
    "SecurityBlockIpViewSet": ("数据源是 Redis key 列表（非 ORM queryset），分页器不参与 ordered 判定，无需 ordering"),
}


def _iter_candidate_classes():
    """遍历仓库源码，产出 ``(相对路径, 类名, 模块路径, 是否声明 ordering)``。"""
    root = pathlib.Path(__file__).resolve().parents[3]
    for path in sorted(root.rglob("*.py")):
        relative = str(path.relative_to(root))
        if any(part in relative for part in SKIP_DIRS) or relative.startswith(FRAMEWORK_DIR):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 语法异常由 ruff/编译期把关
            continue
        module_path = relative[:-3].replace("/", ".")
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {ast.unparse(base).split(".")[-1] for base in node.bases}
            if not bases & LIST_MIXINS:
                continue
            attrs = {
                target.id
                for statement in node.body
                if isinstance(statement, ast.Assign)
                for target in statement.targets
                if isinstance(target, ast.Name)
            }
            yield relative, node.name, module_path, "ordering" in attrs


def _model_ordering(module_path: str, class_name: str):
    """解析视图对应的模型 ``Meta.ordering``（无法解析时返回 None）。"""
    module = importlib.import_module(module_path)
    view = getattr(module, class_name, None)
    if view is None:
        return None
    model = getattr(getattr(view, "queryset", None), "model", None)
    if model is None:
        serializer = getattr(view, "serializer_class", None)
        model = getattr(getattr(serializer, "Meta", None), "model", None)
    return getattr(getattr(model, "_meta", None), "ordering", None)


def test_list_viewsets_declare_default_ordering():
    missing = []
    for relative, class_name, module_path, has_ordering in _iter_candidate_classes():
        if has_ordering or class_name in EXEMPT:
            continue
        if _model_ordering(module_path, class_name):
            continue
        missing.append(f"{relative}::{class_name}")
    assert missing == [], (
        "以下列表 ViewSet 既没有 ordering 也没有模型 Meta.ordering"
        "（分页会抛 UnorderedObjectListWarning 且跨页不稳定）："
        f"{missing}。请加 ordering = [...] 或在 EXEMPT 里登记原因。"
    )


def test_exempt_entries_still_exist():
    """例外清单不能变成「历史垃圾」：登记的类必须仍存在于源码中。"""
    names = {class_name for _path, class_name, _module, _has in _iter_candidate_classes()}
    stale = [name for name in EXEMPT if name not in names]
    assert stale == [], f"例外清单中的类已不存在，请清理：{stale}"
