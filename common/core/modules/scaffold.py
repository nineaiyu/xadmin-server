#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""功能模块脚手架：``{app}/modules.py`` 模板渲染（单一模板源）。

两个命令共用本模块，避免同一文件形态在多处漂移：

- ``manage.py generate_module``（独立生成模块声明）；
- ``manage.py generate_crud --with-module``（生成 CRUD 时顺带声明）。

生成物是普通仓库代码：写入后随 app 安装自动纳入模块清单
（``python manage.py modules``），等级决定各发行预设下是否默认开启。
"""

import json

from .registry import all_module_specs

_TEMPLATE = '''#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""{app_title} 的功能模块声明。

随 app 安装自动纳入模块清单（`python manage.py modules`）；等级决定各发行预设下
是否默认开启：core（不可裁）/ standard（默认开）/ optional（按需开）。
"""

from common.core.modules import ModuleSpec

MODULES = (
    ModuleSpec(
        id="{module_id}",
        label="{label}",
        level="{level}",
{extra}    ),
)
'''


def module_id_conflict(module_id: str) -> bool:
    """模块 id 是否已被内置/已发现的声明占用（generate_module 与 generate_crud 同口径）。"""
    return module_id in {spec.id for spec in all_module_specs()}


def _render_tuple(items) -> str:
    """元组字面量（双引号，与 ruff format 的 quote-style=double 对齐，生成物即过 format）。"""
    body = ", ".join(json.dumps(str(item), ensure_ascii=False) for item in items)
    return f"({body},)" if len(tuple(items)) == 1 else f"({body})"


def render_modules_source(
    *,
    app_title: str,
    module_id: str,
    label: str,
    level: str,
    menus: tuple = (),
    routes: tuple = (),
    permissions: tuple = (),
) -> str:
    """渲染 ``{app}/modules.py`` 源码。

    口径与 ``generate_module`` 既有产物一致：仅声明非空项，生成物需一次通过
    ``ruff check`` + ``ruff format --check``（见生成器单测）。
    """
    extra_lines = []
    if menus:
        extra_lines.append(f"        menus={_render_tuple(menus)},")
    if routes:
        extra_lines.append(f"        routes={_render_tuple(routes)},")
    if permissions:
        extra_lines.append(f"        permissions={_render_tuple(permissions)},")
    return _TEMPLATE.format(
        app_title=app_title,
        module_id=module_id,
        label=label,
        level=level,
        extra="\n".join(extra_lines) + "\n" if extra_lines else "",
    )
