#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : check_adr_status
# author : ly_ix
# date : 2026/10/09
"""ADR 生命周期守护（状态归一 + supersede 链 + 索引状态列一致性）。

口径：

- 每篇 ``docs/adr/ADR-*.md`` 前 ``STATUS_SCAN_LINES`` 行内必须有**归一格式**的状态行
  ``- 状态：<值>``，且 ``<值>`` 首词经别名归一后落在规范状态闭集 ``STATUS_SETS``；
- **supersede 链**：状态为「被取代（Superseded by ADR-0XX）」时——目标 ADR 必须存在、
  链上无环、且双向一致（后继篇正文或 ``adr/README.md`` 的状态行须有「取代 ADR-XXX」注记）；
- **触发制一致性**：状态归一为「暂不实施（触发制）」的 ADR，必须在
  ``docs/plans/触发制任务清单-长期.md`` 中被引用（同名条目）；
- **索引锁步**：``docs/adr/README.md`` 的「状态」列取值须与各文件内状态一致。

边界：本脚本只把**状态行**声明的「被取代」当作整体取代；ADR 之间「落地某决策」「承接某结论」
等演进关系属于**关联**（正文注记），不强制标注为 supersede，也不参与链校验——
二者语义不同，勿混用。

用法::

    python scripts/check_adr_status.py   # 门禁（CI lint.yml doc-facts job）；违例退出码 1
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ADR_DIR = "docs/adr"
ADR_README = "docs/adr/README.md"
TRIGGER_DOC = "docs/plans/触发制任务清单-长期.md"
STATUS_SCAN_LINES = 10

# 归一状态行（文档内）："- 状态：<值>"
STATUS_LINE_RE = re.compile(r"^-\s*状态：\s*(.+?)\s*$")
# adr/README.md 行："| [ADR-001](... ) | <状态> | <主题> |"
README_ROW_RE = re.compile(r"^\|\s*\[(ADR-\d{3})\]\([^)]*\)\s*\|([^|]*)\|")

# 规范状态闭集
STATUS_SETS = ("已交付", "已接受", "暂不实施（触发制）", "评估否", "被取代")
# 状态行首词 → 规范状态的别名归一（不改文档原有措辞，仅用于归类）
STATUS_ALIASES = {
    "已交付": "已交付",
    "已实施": "已交付",
    "已接受": "已接受",
    "部分修订": "已接受",
    "暂不实施": "暂不实施（触发制）",
    "暂不升级": "暂不实施（触发制）",
    "暂不引入": "暂不实施（触发制）",
    "评估否": "评估否",
    "已评估": "评估否",
    "被取代": "被取代",
}
_LEADING_KEYWORD_RE = re.compile(r"^[^\s（(—\-*]+")
_ADR_ID_RE = re.compile(r"ADR-\d{3}")


def _adr_files(root: Path) -> list:
    adr_dir = root / ADR_DIR
    if not adr_dir.is_dir():
        return []
    return sorted(adr_dir.glob("ADR-*.md"))


def _status_value(text: str):
    """前 N 行内的状态行取值（无则 None）。"""
    for line in text.split("\n")[:STATUS_SCAN_LINES]:
        matched = STATUS_LINE_RE.match(line.strip())
        if matched:
            return matched.group(1).strip()
    return None


def canonical_status(value: str):
    """状态取值 → 规范状态（无法归类返回 None）。"""
    stripped = re.sub(r"^[\*\s]+", "", value.strip())
    matched = _LEADING_KEYWORD_RE.match(stripped)
    if not matched:
        return None
    return STATUS_ALIASES.get(matched.group(0))


def supersede_target(value: str):
    """状态取值里声明的被取代目标 ADR id（无则 None）。"""
    matched = _ADR_ID_RE.search(value)
    return matched.group(0) if matched else None


def _readme_status_map(root: Path):
    """adr/README.md 的 ``ADR id → 状态单元格``（无 README 返回 None）。"""
    readme = root / ADR_README
    if not readme.is_file():
        return None
    mapping = {}
    for line in readme.read_text(encoding="utf-8").split("\n"):
        matched = README_ROW_RE.match(line)
        if matched:
            mapping[matched.group(1)] = matched.group(2).strip()
    return mapping


def collect_violations(root: Path = REPO_ROOT):
    """返回 ``(violations, counts, superseded_notes)``（violations 空列表 = 通过）。"""
    violations = []
    counts = {status: 0 for status in STATUS_SETS}
    trigger = root / TRIGGER_DOC
    trigger_text = trigger.read_text(encoding="utf-8") if trigger.is_file() else ""
    readme_status = _readme_status_map(root)

    file_status = {}  # id -> (canonical, value, target, text)
    for path in _adr_files(root):
        adr_id = path.name[:7]
        text = path.read_text(encoding="utf-8")
        value = _status_value(text)
        if value is None:
            violations.append(f"{ADR_DIR}/{path.name}: 前 {STATUS_SCAN_LINES} 行未找到状态行（- 状态：<值>）")
            continue
        canonical = canonical_status(value)
        if canonical is None:
            violations.append(f"{ADR_DIR}/{path.name}: 状态取值「{value}」不在规范闭集 {STATUS_SETS}")
            continue
        target = supersede_target(value) if canonical == "被取代" else None
        counts[canonical] += 1
        file_status[adr_id] = (canonical, value, target, text)

    # —— supersede 链：目标存在 + 无环 + 双向一致 ——
    for adr_id, (canonical, _value, target, _text) in file_status.items():
        if canonical != "被取代":
            continue
        if not target:
            violations.append(f"{ADR_DIR}/{adr_id}: 状态为「被取代」但未声明后继（Superseded by ADR-0XX）")
            continue
        if target not in file_status:
            violations.append(f"{ADR_DIR}/{adr_id}: 声明的后继 {target} 不存在")
            continue
        # 无环：沿链前进，出现重复即环
        seen = [adr_id]
        cursor = target
        while cursor and cursor in file_status and file_status[cursor][0] == "被取代":
            if cursor in seen:
                violations.append(f"{ADR_DIR}/{adr_id}: supersede 链成环（{seen + [cursor]}）")
                break
            seen.append(cursor)
            cursor = file_status[cursor][2]
        # 双向一致：后继篇正文或 README 状态行须有「取代 ADR-XXX」注记
        successor = file_status.get(target)
        note_ok = False
        if successor is not None:
            note_ok = f"取代 {adr_id}" in successor[3]
        if not note_ok and readme_status is not None:
            note_ok = f"取代 {adr_id}" in readme_status.get(target, "")
        if not note_ok:
            violations.append(f"{ADR_DIR}/{adr_id}: 后继 {target} 缺少「取代 {adr_id}」注记（双向一致）")

    # —— 触发制一致性：暂不实施（触发制）须在触发制清单被引用 ——
    for adr_id, (canonical, _value, _target, _text) in file_status.items():
        if canonical == "暂不实施（触发制）" and adr_id not in trigger_text:
            violations.append(f"{ADR_DIR}/{adr_id}: 状态为「暂不实施（触发制）」但未在 {TRIGGER_DOC} 登记条目")

    # —— 索引锁步：README 状态列 ↔ 文件内状态 ——
    if readme_status is not None:
        for adr_id, (canonical, _value, _target, _text) in file_status.items():
            cell = readme_status.get(adr_id)
            if cell is None:
                violations.append(f"{ADR_README}: 索引缺 {adr_id} 行（或未含状态列）")
            elif cell.split("（")[0].strip() != canonical.split("（")[0].strip():
                violations.append(f"{ADR_README}: {adr_id} 状态列「{cell}」与文件内状态「{canonical}」不一致")
    return violations, counts, file_status


def main() -> int:
    violations, counts, file_status = collect_violations()
    print(f"ADR 生命周期校验：{len(file_status)} 篇。")
    print("状态分布：" + " / ".join(f"{status} {counts[status]}" for status in STATUS_SETS))
    if violations:
        print(f"ADR 生命周期违例 {len(violations)} 处：")
        for item in violations:
            print(f"  - {item}")
        print("修复：状态行统一为 `- 状态：<值>` 且取值落在闭集；supersede 链目标存在 / 无环 / 双向一致；")
        print("      触发制项登记进 docs/plans/触发制任务清单-长期.md；README 状态列与文件内状态保持同步。")
        return 1
    print("ADR 生命周期校验通过：状态归一 / supersede 链 / 触发制登记 / 索引状态列全部一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
