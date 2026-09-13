# -*- coding: utf-8 -*-
"""菜单种子数据守护：前端渲染契约类字段的格式校验。

菜单/菜单元数据由 `loadjson/*.json` 作为权威种子导入（`load_init_json`），
格式错误只在"新库/CI 重新种子"后才暴露，所以在种子文件层面加校验。
"""

import json
import os

from django.conf import settings

SEED_MENU_META = os.path.join(settings.PROJECT_DIR, "loadjson", "menumeta.json")


class TestSeedMenuIcons:
    """种子菜单图标必须是 iconify 的 `prefix:name` 格式。

    前端 `useRenderIcon` 以「是否包含 `:`」区分在线/离线图标：无冒号的字符串会被
    当作离线图标集合里的已注册对象，解析不到任何组件直接渲染为空。历史上审批中心 /
    流程定义 / 流程审批三条菜单写成 `ep/stamp`（斜杠）导致左侧菜单没有图标。
    """

    def test_icons_use_iconify_colon_format(self):
        metas = json.load(open(SEED_MENU_META, encoding="utf-8"))
        invalid = []
        for item in metas:
            fields = item.get("fields", {})
            icon = (fields.get("icon") or "").strip()
            # 空图标合法（按钮型菜单节点不需要图标），非空则必须是 prefix:name
            if icon and (":" not in icon or not all(part for part in icon.split(":", 1))):
                invalid.append((item.get("pk"), fields.get("title"), icon))
        assert not invalid, f"菜单图标需为 `prefix:name` 格式（如 ep:user）: {invalid}"
