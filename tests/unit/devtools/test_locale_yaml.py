# -*- coding: utf-8 -*-
"""代码生成器的 locale YAML 合并：只动目标块，不重排人工维护文件。"""

from devtools.management.commands._generate_crud.locale_yaml import merge_locale_yaml

PAYLOAD = {
    "menu_key": "DemoBook",
    "menu_title": "演示书籍",
    "section": "demoBook",
    "labels": {"name": "书籍名称", "isbn": "标准书号"},
}


def test_merge_into_existing_menus_block_keeps_other_keys():
    text = "buttons:\n  ok: 确定\n\nmenus:\n  home: 首页\n\nother:\n  x: y\n"

    merged = merge_locale_yaml(text, **PAYLOAD)

    assert "  home: 首页" in merged
    assert '  DemoBook: "演示书籍"' in merged
    # 新菜单键必须落在 menus 块内、other 块之前
    assert merged.index("menus:") < merged.index("DemoBook") < merged.index("other:")
    assert 'demoBook:\n  name: "书籍名称"\n  isbn: "标准书号"' in merged


def test_merge_replaces_existing_section_and_is_idempotent():
    text = "menus:\n  DemoBook: 旧标题\n\ndemoBook:\n  name: 旧字段\n"
    once = merge_locale_yaml(text, **PAYLOAD)
    twice = merge_locale_yaml(once, **PAYLOAD)

    assert "旧标题" not in once
    assert "旧字段" not in once
    assert once == twice
    assert once.count("menus:") == 1
    assert once.count("demoBook:") == 1
