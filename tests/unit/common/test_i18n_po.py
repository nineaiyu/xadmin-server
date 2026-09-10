# -*- coding: utf-8 -*-
"""服务端中文语言包门禁：msgid 必须有 zh 译文，且无 fuzzy / 无重复。

背景：新增 gettext 文案后若漏同步 ``locale/zh/LC_MESSAGES/django.po``，中文界面会
静默回退英文——功能测试通常断言 code/结构而非文案，这类漏翻看不出来。故在此加硬门禁
（与前端 ``src/tests/locale-keys.spec.ts`` 的语言包 key 一致性门禁同思路）。
"""

import re
from collections import Counter
from pathlib import Path

PO_PATH = Path(__file__).resolve().parents[3] / "locale/zh/LC_MESSAGES/django.po"

EMPTY = ("", '""')


def _iter_entries(text: str):
    """解析 po 条目，产出 ``(msgid, msgstr, is_fuzzy, is_obsolete)``。

    只做本门禁需要的解析：支持引号续行的多行拼接，忽略 msgctxt；
    废弃条目（``#~`` 前缀）不参与校验。
    """
    for block in re.split(r"\n\s*\n", text):
        lines = block.strip().splitlines()
        if not lines:
            continue
        obsolete = lines[0].startswith("#~")
        fuzzy = any(line.startswith("#,") and "fuzzy" in line for line in lines)
        msgid = msgstr = ""
        mode = None
        for line in lines:
            if line.startswith("msgid "):
                mode, msgid = "id", line[6:].strip()
            elif line.startswith("msgstr "):
                mode, msgstr = "str", line[7:].strip()
            elif line.startswith('"') and mode:
                if mode == "id":
                    msgid += line.strip()
                else:
                    msgstr += line.strip()
        yield msgid, msgstr, fuzzy, obsolete


def _entries():
    assert PO_PATH.exists(), f"中文语言包不存在：{PO_PATH}"
    return [
        (msgid, msgstr, fuzzy)
        for msgid, msgstr, fuzzy, obsolete in _iter_entries(PO_PATH.read_text(encoding="utf-8"))
        if not obsolete and msgid not in EMPTY
    ]


def test_all_msgids_have_chinese_translation():
    missing = [msgid for msgid, msgstr, fuzzy in _entries() if not fuzzy and msgstr in EMPTY]
    assert missing == [], f"以下 msgid 缺中文译文（中文界面会回退英文）：{missing[:20]}"


def test_no_fuzzy_entries():
    fuzzy = [msgid for msgid, _msgstr, is_fuzzy in _entries() if is_fuzzy]
    assert fuzzy == [], f"存在 fuzzy 条目（不会生效，等同漏翻）：{fuzzy[:20]}"


def test_no_duplicate_msgid():
    duplicates = [msgid for msgid, count in Counter(msgid for msgid, _s, _f in _entries()).items() if count > 1]
    assert duplicates == [], f"存在重复 msgid（后者覆盖前者，易漏翻）：{duplicates[:20]}"
