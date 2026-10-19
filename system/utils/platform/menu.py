#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : menu
# author : ly_13
# date : 10/29/2024
from typing import Any

from django.contrib.auth.models import Group, Permission
from django.utils.module_loading import import_string
from rest_framework.routers import SimpleRouter
from rest_framework.viewsets import GenericViewSet

from common.core.modelset import NoDetailModelSet
from common.core.routers import NoDetailRouter
from common.core.utils import get_all_url_dict, get_doc_first_line
from common.utils import get_logger

router = SimpleRouter(False)
no_detail_router = NoDetailRouter(False)
logger = get_logger(__file__)

#: 权限点标题（MenuMeta.title）的长度上限：该标题是菜单树/候选列表里的单行显示项，
#: 超长会在树形与批量授权选择器里撑破布局。docstring 首行的口径见
#: docs/architecture/handbook-backend.md（首行 = 菜单显示名，细节下沉后续段落）。
MENU_TITLE_MAX = 24


def _truncate_title(text: str, limit: int = MENU_TITLE_MAX) -> str:
    """把标题收敛到 ``limit`` 字内，并去掉结尾句号（菜单标题是名词短语，不是句子）。

    优先在句读边界切，保语义完整；无边界则硬截加省略号。返回长度**保证** ≤ ``limit``
    （省略号计入），调用方不需再截。
    """
    text = (text or "").strip().rstrip("。")
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    positions = [head.find(sep) for sep in ("。", "；", ";", "，", ",", ":", "：")]
    positions = [position for position in positions if position >= 4]
    if positions:
        return head[: min(positions)]
    return f"{head}…"


def get_long_str(li: Any) -> Any:
    result = ""
    for i in zip(*li, strict=False):
        if len(set(i)) == 1:
            result += i[0]
        else:
            break
    return result


def get_related_models(model: Any) -> Any:
    related_models = {model._meta.label_lower}
    for field in model._meta._get_fields(reverse=False):
        if (
            field.is_relation
            and field.related_model
            and not issubclass(field.related_model, (Group, Permission))
            and field.name not in ["creator", "modifier", "dept_belong"]
        ):
            related_models.add(field.related_model._meta.label_lower)
    return related_models


def get_view_permissions(view_string: Any, code_suffix: Any = "") -> Any:
    permissions: list[dict[str, Any]] = []

    url_paths = [url for url in get_all_url_dict("") if url.get("view") == view_string]
    if not url_paths:
        return permissions
    view_set = import_string(url_paths[0].get("view"))

    names = []
    for url_path in url_paths:
        name = url_path.get("name")
        for s in ["-list", "-detail"]:
            if name.endswith(s):
                name = name[: -len(s)]
        names.append(name)

    basename = get_long_str(names).rstrip("-")
    is_view_set = True
    models = []
    try:
        if issubclass(view_set, GenericViewSet):
            if issubclass(view_set, NoDetailModelSet):
                routers = no_detail_router.get_routes(view_set)
            else:
                routers = router.get_routes(view_set)
            route_info = {r.name.format(basename=basename): r.mapping for r in routers}
            try:
                models = get_related_models(view_set.queryset.model)
            except Exception as e:
                logger.error(f"get_related_models {view_set} failed {e}", exc_info=True)
                pass
        else:
            is_view_set = False
            route_mapping = no_detail_router.routes[0].mapping
            route_info = {
                url_paths[0].get("name"): {
                    item.lower(): route_mapping[item.lower()] for item in set(view_set().allowed_methods) - {"OPTIONS"}
                }
            }
    except Exception as e:
        logger.warning(f"Exception while getting permissions for {view_string}: {e}")
        return permissions

    view_doc = get_doc_first_line(view_set.__doc__)

    for url_path in url_paths:
        methods = route_info.get(url_path.get("name"), {})
        for method, func_name in methods.items():
            if is_view_set and method.lower() == "put":  # 忽略view set的put请求，使用patch请求
                continue
            try:
                action_doc = get_doc_first_line(getattr(view_set, func_name if is_view_set else method).__doc__)
            except Exception as e:
                logger.warning(f"Exception while getting action doc for {view_string}: {e}")
                continue
            if action_doc:
                try:
                    action_doc = action_doc.format(cls=_truncate_title(view_doc))
                except Exception:
                    # docstring 含未知占位符：回退为视图摘要
                    action_doc = view_doc
            else:
                action_doc = view_doc

            code = func_name.title().replace("_", "").replace("-", "")
            permissions.append(
                {
                    "method": method.upper(),
                    "url": url_path.get("url"),
                    "code": f"{code[0].lower()}{code[1:]}:{code_suffix}",
                    "description": _truncate_title(action_doc) if action_doc else view_set.__name__,
                    "models": models if func_name in ["list", "create", "retrieve", "update", "partial_update"] else [],
                }
            )

    return permissions
