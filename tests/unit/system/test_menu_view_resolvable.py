# -*- coding: utf-8 -*-
"""菜单 component 可解析守护：后端下发的每个页面组件路径必须命中前端真实视图。

背景：菜单由 `loadjson/menu.json` 下发、前端按 `component` 懒加载视图；组件路径
写错或视图改名后，菜单只是"点进去 404/空白"（历史遗留：任务中心菜单指向的
`system/task/index` 视图已不存在，属死配置）。

判定与前端 `resolveComponentKey` 同源：精确候选（.vue/.tsx/index.vue/index.tsx）
优先，再退回包含匹配；仅校验 menu_type=1（菜单）且 component 非外链的条目。
单仓检出（无 xadmin-client）时自动跳过，与 CSP 策略同步守护同口径。
"""

import json
import os
from pathlib import Path

import pytest
from django.conf import settings

SEED_MENU = os.path.join(settings.PROJECT_DIR, "loadjson", "menu.json")
CLIENT_VIEWS = Path(settings.PROJECT_DIR).parent / "xadmin-client" / "src" / "views"


def _view_keys() -> set:
    return {
        f"/src/views/{path.relative_to(CLIENT_VIEWS).as_posix()}"
        for path in CLIENT_VIEWS.rglob("*")
        if path.is_file() and path.suffix in (".vue", ".tsx") and "node_modules" not in path.parts
    }


def _resolvable(component: str, keys: set) -> bool:
    base = f"/src/views/{component.lstrip('/')}"
    for candidate in (base, f"{base}.vue", f"{base}.tsx", f"{base}/index.vue", f"{base}/index.tsx"):
        if candidate in keys:
            return True
    # 前端宽松兜底：路径片段包含匹配（兼容 component 与目录结构非严格前缀的既有写法）
    return any(component in key for key in keys)


@pytest.mark.skipif(not CLIENT_VIEWS.is_dir(), reason="单仓检出（缺少 xadmin-client 视图目录）")
class TestMenuViewResolvable:
    def test_every_menu_component_resolves_to_view(self):
        keys = _view_keys()
        menus = json.load(open(SEED_MENU, encoding="utf-8"))
        missing = []
        for row in menus:
            fields = row.get("fields", {})
            if fields.get("menu_type") != 1:
                continue
            component = (fields.get("component") or "").strip()
            if not component or component.startswith("http"):
                continue
            if not _resolvable(component, keys):
                missing.append((fields.get("name"), fields.get("path"), component))
        assert missing == [], f"以下菜单的 component 无法解析到前端视图（点击即 404）：{missing}"
