#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : check_docs_site_nav
# author : ly_ix
# date : 2026/09/20
"""文档站导航覆盖守护（防"孤儿页面"，xadmin-docs 侧）。

口径：``xadmin-docs`` 内容目录（guide / advanced / example / problem / devguidelines）
与根级页面下的 ``*.md`` 必须出现在站点导航（``.vitepress/config.mts`` 的 ``link: '...'``）中——
新增页面忘记接入 sidebar/nav 时 CI 失败（页面无入口可达，只能被站内搜索偶然命中）。

惯例豁免：``README.md``（仓库说明）；``index.md`` 允许以目录 link（如 ``/guide``）指代；
根级 ``index.md``（站点首页）由 ``link: '/'`` 承载。
跨仓根（``XADMIN_DOCS_DIR``，默认 ``../xadmin-docs``）缺失时跳过（单仓检出守卫）。

用法::

    python scripts/check_docs_site_nav.py   # 文档站 CI（docs-build.yml）；违例退出码 1
"""

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

NAV_DIRS = ("guide", "advanced", "example", "problem", "devguidelines")
LINK_RE = re.compile(r"link:\s*'([^']+)'")


def _docs_root() -> Path:
    """跨仓文档根（xadmin-docs）：环境变量优先，默认同工作区兄弟目录。"""
    return Path(os.environ.get("XADMIN_DOCS_DIR", REPO_ROOT.parent / "xadmin-docs"))


def _normalized_links(config_text: str) -> set:
    """config.mts 的 link 归一化集合（去锚点/查询串/首尾斜杠）。"""
    links = set()
    for raw in LINK_RE.findall(config_text):
        link = raw.split("#")[0].split("?")[0].strip().strip("/")
        links.add(link)
    return links


def collect_violations(docs_root: Path | None = None) -> list:
    """返回违例清单（空列表 = 通过）；``docs_root`` 参数供测试注入。"""
    root = docs_root if docs_root is not None else _docs_root()
    config = root / ".vitepress" / "config.mts"
    if not config.is_file():
        return []  # 跨仓未检出（或站点结构变化）：跳过而非误报
    links = _normalized_links(config.read_text(encoding="utf-8"))
    violations = []
    # 根级页面（首页由 link: '/' 承载、README 为仓库说明，均豁免）
    for path in sorted(root.glob("*.md")):
        if path.name in ("README.md", "index.md") or path.stem in links:
            continue
        violations.append(f"xadmin-docs:{path.name}: 未接入站点导航（.vitepress/config.mts 的 link）")
    # 内容目录
    for sub in NAV_DIRS:
        directory = root / sub
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if path.name == "README.md":
                continue
            candidates = {f"{sub}/{path.stem}"}
            if path.stem == "index":
                candidates.add(sub)
            if candidates & links:
                continue
            violations.append(f"xadmin-docs:{sub}/{path.name}: 未接入站点导航（.vitepress/config.mts 的 link）")
    return violations


def main() -> int:
    violations = collect_violations()
    if violations:
        print(f"文档站导航覆盖：{len(violations)} 个孤儿页面：")
        for item in violations:
            print(f"  - {item}")
        print("修复：在 xadmin-docs/.vitepress/config.mts 的 nav/sidebar 中接入该页面后重跑。")
        return 1
    print("文档站导航覆盖通过：全部内容页面已接入站点导航。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
