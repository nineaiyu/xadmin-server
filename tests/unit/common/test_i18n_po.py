# -*- coding: utf-8 -*-
"""服务端中文语言包门禁：msgid 必须有 zh 译文，且无 fuzzy / 无重复。

背景：新增 gettext 文案后若漏同步 ``locale/zh/LC_MESSAGES/django.po``，中文界面会
静默回退英文——功能测试通常断言 code/结构而非文案，这类漏翻看不出来。故在此加硬门禁
（与前端 ``src/tests/locale-keys.spec.ts`` 的语言包 key 一致性门禁同思路）。
"""

import ast
import re
from collections import Counter
from pathlib import Path

PO_PATH = Path(__file__).resolve().parents[3] / "locale/zh/LC_MESSAGES/django.po"
EN_PO_PATH = Path(__file__).resolve().parents[3] / "locale/en/LC_MESSAGES/django.po"
CAPTCHA_SOURCES = (
    Path(__file__).resolve().parents[3] / "captcha/serializers.py",
    Path(__file__).resolve().parents[3] / "captcha/models.py",
    Path(__file__).resolve().parents[3] / "settings/serializers/security_captcha.py",
)

EMPTY = ("", '""')


def _po_text(raw: str) -> str:
    """去掉 po 字符串的外层引号并还原常见转义（多行片段逐段拼接用）。"""
    raw = raw.strip()
    if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    return raw.replace('\\"', '"').replace("\\n", "\n")


def _iter_entries(text: str):
    """解析 po 条目，产出 ``(msgid, msgstr, is_fuzzy, is_obsolete)``。

    只做本门禁需要的解析：支持引号续行的多行拼接（``msgid ""`` + 续行是
    手工追加条目的常见写法），忽略 msgctxt；废弃条目（``#~`` 前缀）不参与校验。
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
                mode, msgid = "id", _po_text(line[6:])
            elif line.startswith("msgstr "):
                mode, msgstr = "str", _po_text(line[7:])
            elif line.startswith('"') and mode:
                if mode == "id":
                    msgid += _po_text(line)
                else:
                    msgstr += _po_text(line)
        yield msgid, msgstr, fuzzy, obsolete


def _entries(path=PO_PATH):
    assert path.exists(), f"语言包不存在：{path}"
    return [
        (msgid, msgstr, fuzzy)
        for msgid, msgstr, fuzzy, obsolete in _iter_entries(path.read_text(encoding="utf-8"))
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


def test_en_catalog_has_no_fuzzy_entries():
    """英文语言包不得残留 fuzzy 条目（与中文门禁同口径）。

    en 目录是部分翻译目录：空 msgstr 属正常（回落到英文原文 msgid）；但 fuzzy
    条目携带的是旧 msgid 的陈旧译文，一旦误去标记就会启用错文案——存量 88 条
    已清理，此处设门禁防止 makemessages 回潮。
    """
    fuzzy = [msgid for msgid, _msgstr, is_fuzzy in _entries(EN_PO_PATH) if is_fuzzy]
    assert fuzzy == [], f"en 语言包存在 fuzzy 条目（陈旧译文，不会生效）：{fuzzy[:20]}"


def test_en_catalog_no_duplicate_msgid():
    duplicates = [
        msgid for msgid, count in Counter(msgid for msgid, _s, _f in _entries(EN_PO_PATH)).items() if count > 1
    ]
    assert duplicates == [], f"en 语言包存在重复 msgid：{duplicates[:20]}"


def _source_gettext_messages(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    messages: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            messages.add(node.args[0].value)
    return messages


def test_captcha_source_messages_have_catalog_entries():
    """captcha 与验证码设置序列化器的 ``_()`` 必须全部进 zh po。

    全仓源码扫描范围太大，先把新增验证码域纳入定向门禁；新增文案漏翻时
    直接失败，而不是等运行中发现中文界面回退英文。
    """
    messages: set[str] = set()
    for source in CAPTCHA_SOURCES:
        messages |= _source_gettext_messages(source)
    catalog = {msgid for msgid, _msgstr, _fuzzy in _entries()}
    missing = sorted(messages - catalog)
    assert missing == [], f"验证码源码文案缺 zh 词条：{missing[:20]}"
