# -*- coding: utf-8 -*-
"""菜单种子数据守护：前端渲染契约类字段的格式校验。

菜单/菜单元数据由 `loadjson/*.json` 作为权威种子导入（`load_init_json`），
格式错误只在"新库/CI 重新种子"后才暴露，所以在种子文件层面加校验。
"""

import json
import os
import re
from pathlib import Path

import pytest
from django.conf import settings

SEED_MENU = os.path.join(settings.PROJECT_DIR, "loadjson", "menu.json")
SEED_MENU_META = os.path.join(settings.PROJECT_DIR, "loadjson", "menumeta.json")
CLIENT_LOCALES = Path(settings.PROJECT_DIR).parent / "xadmin-client" / "locales"


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


class TestSeedNavigationTitles:
    """导航类（目录/菜单）标题必须是 i18n key。

    前端对菜单标题执行 transformI18n（语言包 key → 文案，找不到 key 时原样返回）；
    直接写中文会让英文界面显示中文（历史上「数据查询 / 在线用户 / API 应用」三条如此，
    对应语言包 key 为 menus.searchData / menus.userOnline / menus.apiApp）。
    权限点（menu_type=2）标题是内部授权标识（中英混排沿用既有命名），不在校验范围。
    """

    def test_navigation_titles_are_i18n_keys(self):
        menus = json.load(open(SEED_MENU, encoding="utf-8"))
        metas = {item["pk"]: item.get("fields", {}) for item in json.load(open(SEED_MENU_META, encoding="utf-8"))}
        invalid = []
        for row in menus:
            fields = row.get("fields", {})
            if fields.get("menu_type") not in (0, 1):
                continue  # 0=目录 1=菜单 2=权限点
            title = (metas.get(fields.get("meta"), {}).get("title") or "").strip()
            if title and not re.match(r"^[A-Za-z][\w]*\.[\w.]+$", title):
                invalid.append((fields.get("name"), title))
        assert not invalid, f"导航菜单标题需使用 i18n key（形如 menus.xxx）: {invalid}"


class TestMenuI18nKeysExistInClient:
    """菜单 i18n key 必须存在于前端语言包（单仓检出自动跳过，与 CSP 策略同步守护同口径）。

    后端 menumeta 下发 key（如 ``menus.searchData``），前端 transformI18n 找不到 key 时
    原样显示 key 字符串——跨仓漂移只在真实页面暴露，故在种子层面加守护。
    """

    @pytest.mark.skipif(not CLIENT_LOCALES.is_dir(), reason="单仓检出（缺少 xadmin-client 语言包）")
    def test_menu_title_keys_present_in_client_locales(self):
        import yaml

        menus = json.load(open(SEED_MENU, encoding="utf-8"))
        metas = {item["pk"]: item.get("fields", {}) for item in json.load(open(SEED_MENU_META, encoding="utf-8"))}
        keys = set()
        for row in menus:
            fields = row.get("fields", {})
            title = (metas.get(fields.get("meta"), {}).get("title") or "").strip()
            if re.match(r"^[A-Za-z][\w]*\.[\w.]+$", title):
                keys.add(title)
        assert keys, "未提取到任何菜单 i18n key，检查种子结构"

        def resolve(data, key):
            node = data
            for part in key.split("."):
                if not isinstance(node, dict) or part not in node:
                    return False
                node = node[part]
            return True

        for filename in ("zh-CN.yaml", "en.yaml"):
            data = yaml.safe_load((CLIENT_LOCALES / filename).read_text(encoding="utf-8"))
            missing = sorted(key for key in keys if not resolve(data, key))
            assert not missing, f"{filename} 缺少菜单词条：{missing}"
