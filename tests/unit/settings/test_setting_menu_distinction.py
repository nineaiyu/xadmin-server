# -*- coding: utf-8 -*-
"""系统设置页与系统配置页的菜单标题回归。

两页历史上都叫「系统设置 / 系统配置」，仅一字之差且能力相反（前者只读台账、
后者完整 CRUD）。旧页已降级为只读台账并把菜单标题改为「设置台账」；
本用例锁定两个 title key 不再相同，避免后续种子回写把标题改回近义名。
"""

import json
from pathlib import Path

from django.conf import settings

ROOT = Path(settings.PROJECT_DIR)


def _page_title(app: str, menu_name: str) -> str:
    menu_rows = json.loads((ROOT / app / "seeds" / "menu.json").read_text(encoding="utf-8"))
    meta_rows = json.loads((ROOT / app / "seeds" / "menumeta.json").read_text(encoding="utf-8"))
    menu = next(row for row in menu_rows if row["fields"].get("name") == menu_name)
    meta = next(row for row in meta_rows if row["pk"] == menu["fields"]["meta"])
    return meta["fields"]["title"]


def test_setting_ledger_and_system_config_titles_are_distinct():
    ledger = _page_title("settings", "SystemSetting")
    config = _page_title("system", "SystemConfig")

    assert ledger == "menus.settingLedger"
    assert config == "menus.systemConfigManagement"
    assert ledger != config
