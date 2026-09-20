#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : check_doc_facts
# author : ly_ix
# date : 2026/09/19
"""文档事实校验（单一事实源防漂移，G4）。

口径：**代码是事实源，文档描述"当前状态"的版本串必须与代码一致**。
事实源：

- ``requirements.txt``（Django / DRF 等依赖版本）；
- ``server/const.py`` 的 ``VERSION``（平台版本）。

校验面只圈定「当前事实」文档（``FACTS`` 表逐条登记），不扫描全文：
历史类内容（ADR 决策记录 / plans 台账 / 归档）里的旧版本串是**当时的事实**，
不属于漂移；"X.Y.Z 起"这类追溯性表述跨版本仍然为真，也不校验。

用法::

    python scripts/check_doc_facts.py   # 门禁（CI lint.yml）；违例退出码 1

新增受保护事实：往 ``FACTS`` 登记一条（文档 / 标签 / 单捕获组正则 / 事实源）。
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 受保护事实：文档中的「当前状态」断言 ↔ 代码事实源
# doc     相对仓库根的文档路径；
# label   事实名（输出用）；
# pattern 单捕获组正则（文档内全部命中都必须等于事实源的值；零命中也算失败，
#         防止文档改版后受保护事实被悄悄丢掉）；
# source  事实源："requirements:<小写包名>" 或 "const:VERSION"
FACTS = [
    {
        "doc": "docs/architecture/overview.md",
        "label": "技术栈表 Django 版本",
        "pattern": r"Django (\d+\.\d+\.\d+) \+ DRF",
        "source": "requirements:django",
    },
    {
        "doc": "docs/architecture/overview.md",
        "label": "技术栈表 DRF 版本",
        "pattern": r"\+ DRF (\d+\.\d+\.\d+)",
        "source": "requirements:djangorestframework",
    },
    {
        "doc": "docs/architecture/overview.md",
        "label": "ADR-004 摘要「当前运行 Django」",
        "pattern": r"当前运行 Django (\d+\.\d+\.\d+)",
        "source": "requirements:django",
    },
    {
        "doc": "docs/README.md",
        "label": "索引 ADR-004 行「当前运行」",
        "pattern": r"当前运行 (\d+\.\d+\.\d+)；",
        "source": "requirements:django",
    },
    {
        "doc": "docs/ops/deployment.md",
        "label": "「适用于 xadmin-server X.Y.Z+」",
        "pattern": r"适用于 xadmin-server (\d+\.\d+\.\d+)\+",
        "source": "const:VERSION",
    },
]


def _requirements_versions() -> dict:
    """requirements.txt 的 ``包名(小写) → 版本``（跳过注释与范围声明）。"""
    versions = {}
    for line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, _, version = line.partition("==")
        versions[name.strip().lower()] = version.strip()
    return versions


def _const_version() -> str:
    text = (REPO_ROOT / "server" / "const.py").read_text(encoding="utf-8")
    matched = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if not matched:
        raise SystemExit(f"无法从 server/const.py 解析 VERSION：{REPO_ROOT / 'server' / 'const.py'}")
    return matched.group(1)


def _resolve_source(source: str) -> str:
    kind, _, key = source.partition(":")
    if kind == "requirements":
        versions = _requirements_versions()
        if key not in versions:
            raise SystemExit(f"requirements.txt 中未找到 {key}（事实源缺失，请核对 FACTS）")
        return versions[key]
    if kind == "const" and key == "VERSION":
        return _const_version()
    raise SystemExit(f"未知事实源：{source}（支持 requirements:<pkg> / const:VERSION）")


def collect_violations(root: Path = REPO_ROOT, facts=None) -> list:
    """返回违例清单（空列表 = 通过）；``facts`` 参数供测试注入。"""
    violations = []
    for fact in facts if facts is not None else FACTS:
        doc = root / fact["doc"]
        if not doc.is_file():
            violations.append(f"{fact['doc']}: 文档不存在（受保护事实 {fact['label']} 失去载体）")
            continue
        expected = _resolve_source(fact["source"])
        matches = list(re.finditer(fact["pattern"], doc.read_text(encoding="utf-8")))
        if not matches:
            violations.append(
                f"{fact['doc']}: 未匹配到受保护事实「{fact['label']}」"
                f"（正则 {fact['pattern']}；文档改版后请同步更新 FACTS）"
            )
            continue
        for matched in matches:
            actual = matched.group(1)
            if actual != expected:
                line_no = doc.read_text(encoding="utf-8").count("\n", 0, matched.start()) + 1
                violations.append(
                    f"{fact['doc']}:{line_no}: 「{fact['label']}」文档写 {actual}，"
                    f"代码事实源为 {expected}（{fact['source']}）"
                )
    return violations


def main() -> int:
    violations = collect_violations()
    checked = len(FACTS)
    if violations:
        print(f"文档事实校验：{checked} 条受保护事实，{len(violations)} 条违例：")
        for item in violations:
            print(f"  - {item}")
        print("修复：以代码事实源（requirements.txt / server/const.py）为准更新文档；")
        print("      若为文档结构变化，同步维护 scripts/check_doc_facts.py 的 FACTS 表。")
        return 1
    print(f"文档事实校验通过：{checked} 条受保护事实与代码一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
