#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""菜单种子的路由字段守护：页面菜单必须带组件路径，目录不得带脏组件。

前端 `addAsyncRoutes` 按后端下发的 `component` 解析 `src/views` 下的页面文件：

- 菜单型节点（menu_type=1）漏配 component → 打开即空白路由（真故障，用户可见空白页）；
- 目录型节点（menu_type=0）带 component → 该值无人消费，且历史脏值会在开发态触发
  「动态路由组件未匹配」误报（`/system/notice/` 曾挂着 `system/notify/index`）。

两者都在此按**种子文件**校验（不依赖数据库，CI 单仓检出同样生效），
避免"新装库带着脏字段上线"。
"""

import json
import os

from django.conf import settings

MENU_SEED_FILE = "menu.json"


def _frontend_nodes():
    """种子里的前端路由节点（menu_type 0=目录 / 1=菜单，且 path 为前端路径）。"""
    path = os.path.join(settings.PROJECT_DIR, "loadjson", MENU_SEED_FILE)
    with open(path, encoding="utf-8") as fp:
        rows = json.load(fp)
    for row in rows:
        fields = row["fields"]
        if fields.get("menu_type") not in (0, 1):
            continue
        if not str(fields.get("path") or "").startswith("/"):
            continue
        yield fields


class TestMenuSeedRouteComponents:
    def test_page_menus_have_component(self):
        """菜单型节点必须有组件路径（漏配的表现为点击后空白页）。"""
        missing = [
            f"{fields['path']}({fields['name']})"
            for fields in _frontend_nodes()
            if fields.get("menu_type") == 1 and not str(fields.get("component") or "").strip()
        ]
        assert not missing, "菜单型节点缺少组件路径（打开为空白页）：\n" + "\n".join(missing)

    def test_directories_have_no_component(self):
        """目录型节点不应配置组件路径（脏值会被前端当作待解析组件而误报未匹配）。"""
        dirty = [
            f"{fields['path']}({fields['name']}) -> {fields['component']}"
            for fields in _frontend_nodes()
            if fields.get("menu_type") == 0 and str(fields.get("component") or "").strip()
        ]
        assert not dirty, "目录型节点不应配置组件路径（脏值会触发前端误报）：\n" + "\n".join(dirty)
