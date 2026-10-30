#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""模块脚手架的种子骨架渲染（generate_module 的 --with-seeds 支撑）。

种子四件套（manifest / menu / menumeta / fieldpermission+modellabelfield 空容器）
与 ``ai_declarations.py`` 骨架由本模块渲染。**权限点行与字段树行不在这里生成**：
容器灌库后由既有 ``sync_menu_permissions --update-seed --default-parent <菜单>``
按路由扫描回写、字段树由 ``sync_model_field`` 同步——脚手架只负责
「容器 + 页面根 + 登记清单」，与回写工具各司其职（此前这三步全靠手抄示例改）。

主键用 uuid5(app+模型+name) 确定性生成：重复执行幂等，不产生种子漂移。
"""

import json
import re
import uuid
from typing import Any

#: 种子主键命名空间（与业务数据解耦：重装/换库不依赖库内主键）
_SEED_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "xadmin.module_scaffold")

#: 骨架菜单行的固定时间戳（种子是声明式契约，不携带生成时刻）
_SEED_STAMP = "2024-01-01T00:00:00Z"

#: manifest 文件登记次序（与内置 app 种子同口径：meta 先于 menu 装载）
_MANIFEST_FILES = (
    ("menumeta.json", "system.menumeta", "contract", 10),
    ("menu.json", "system.menu", "contract", 20),
    ("fieldpermission.json", "system.fieldpermission", "contract", 50),
    ("modellabelfield.json", "system.modellabelfield", "generated", 60),
)


def seed_pk(model: str, app: str, name: str) -> str:
    """种子行确定性主键：同 (app, model, name) 重复生成得到同一 pk。"""
    return str(uuid.uuid5(_SEED_NS, f"{model}:{app}:{name}"))


def url_prefixes_of(routes: tuple[str, ...]) -> list[str]:
    """路由正则 → manifest 的 url_prefixes（去 ``^``/``$`` 锚点与前导斜杠）。"""
    prefixes = []
    for route in routes:
        prefix = route.strip().lstrip("^").lstrip("/").rstrip("$")
        if prefix and prefix not in prefixes:
            prefixes.append(prefix)
    return prefixes


def _snake(name: str) -> str:
    """驼峰菜单名 → 蛇形路径段（MyBizPage → my_biz_page）。"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _meta_row(app: str, name: str, title: str) -> dict[str, Any]:
    return {
        "model": "system.menumeta",
        "pk": seed_pk("system.menumeta", app, name),
        "fields": {
            "created_time": _SEED_STAMP,
            "updated_time": _SEED_STAMP,
            "description": None,
            "creator": 1,
            "modifier": 1,
            "dept_belong": None,
            "title": title,
            "icon": "",
            "r_svg_name": "",
            "is_show_menu": True,
            "is_show_parent": False,
            "is_keepalive": True,
            "frame_url": "",
            "frame_loading": False,
            "watermark": False,
            "transition_enter": "",
            "transition_leave": "",
            "is_hidden_tag": False,
            "fixed_tag": False,
            "dynamic_level": 0,
            "active_path": None,
            "badge": None,
            "badge_type": "normal",
            "query": None,
            "group_name": None,
            "group_rank": None,
        },
    }


def _menu_row(app: str, name: str, rank: int) -> dict[str, Any]:
    """页面根骨架行：顶级菜单（parent=null），path/component 为占位、待按实际前端调整。"""
    segment = _snake(name)
    return {
        "model": "system.menu",
        "pk": seed_pk("system.menu", app, name),
        "fields": {
            "created_time": _SEED_STAMP,
            "updated_time": _SEED_STAMP,
            "description": "generate_module 骨架：按实际前端路由调整 path/component，需挂到目录时改 parent 为目录 pk",
            "creator": 1,
            "modifier": 1,
            "dept_belong": None,
            "deleted_at": None,
            "parent": None,
            "menu_type": 1,
            "name": name,
            "rank": rank,
            "path": f"/{app}/{segment}/index",
            "component": f"{app}/{segment}/index",
            "is_active": True,
            "meta": seed_pk("system.menumeta", app, name),
            "method": None,
            "model": [],
        },
    }


def _manifest(app: str, routes: tuple[str, ...]) -> dict[str, Any]:
    return {
        "version": 1,
        "app": app,
        "depends": [],
        "url_prefixes": url_prefixes_of(routes),
        "config_keys": [],
        "files": [
            {"name": name, "model": model, "kind": kind, "order": order} for name, model, kind, order in _MANIFEST_FILES
        ],
    }


def _ai_declarations(app: str, module_id: str) -> str:
    return f'''#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""{app} 的 AI 动作声明骨架（generate_module 生成）。

无对外动作可整文件删除；有动作时按 task/ai_declarations.py 的声明式写法补充，
``modules=("{module_id}",)`` 让动作随本模块裁剪。
"""

# 示例（按真实端点改写后启用）：
# from django.utils.translation import gettext_lazy as _
#
# from ai.services.actions.ai_api_actions import IN_QUERY, api_action
#
# AI_ACTIONS = {{
#     "{module_id}.list": api_action(
#         key="{module_id}.list",
#         label=_("List records"),
#         description=_("List records of {module_id}"),
#         method="GET",
#         path="/api/{app}/",
#         params={{}},
#         modules=("{module_id}",),
#     ),
# }}

AI_ACTIONS = {{}}
'''


def render_seed_skeleton(
    app: str, module_id: str, label: str, menus: tuple[str, ...], routes: tuple[str, ...]
) -> dict[str, str]:
    """渲染种子四件套 + ai_declarations 骨架（返回 {相对路径: 文件内容}）。

    菜单只生成「页面根」（每个 ``--menu`` 一行，顶级挂载）；权限点行由
    ``sync_menu_permissions --update-seed`` 按路由扫描回写，字段树由
    ``sync_model_field`` 同步——此处生成的 fieldpermission/modellabelfield
    是空容器，仅完成 manifest 登记让回写有落点。
    """
    metas = [_meta_row(app, name, label if len(menus) == 1 else f"{label}·{name}") for name in menus]
    menu_rows = [_menu_row(app, name, 900 + index) for index, name in enumerate(menus)]
    return {
        "seeds/manifest.json": json.dumps(_manifest(app, routes), ensure_ascii=False, indent=1) + "\n",
        "seeds/menumeta.json": json.dumps(metas, ensure_ascii=False, indent=1) + "\n",
        "seeds/menu.json": json.dumps(menu_rows, ensure_ascii=False, indent=1) + "\n",
        "seeds/fieldpermission.json": "[]\n",
        "seeds/modellabelfield.json": "[]\n",
        "ai_declarations.py": _ai_declarations(app, module_id),
    }
