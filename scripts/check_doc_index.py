#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : check_doc_index
# author : ly_ix
# date : 2026/09/20
"""文档索引覆盖守护（防"孤儿文档"）。

口径：``docs/`` 下的"内容型文档"必须被索引引用——出现在 ``docs/README.md``
或同目录 ``README.md``（如 ``plans/README.md``）中。新增文档忘记登记索引时
CI 失败，避免文档成为无人可达的孤儿。

范围：``docs/``（根级，不含 README 自身）/ ``architecture/`` / ``guide/`` /
``ops/`` / ``adr/`` / ``plans/``（``plans/archive/`` 为历史归档，不要求登记）。
跨仓（可选）：``xadmin-client/docs/*.md`` 必须被 ``xadmin-client/docs/README.md``
引用；客户端仓库缺失时跳过（单仓检出守卫，``XADMIN_CLIENT_DIR`` 可覆盖根路径）。

用法::

    python scripts/check_doc_index.py   # 门禁（CI lint.yml）；违例退出码 1
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 需要索引覆盖的 docs 子目录（"" = docs 根级）
INDEXED_DIRS = ("", "architecture", "guide", "ops", "adr", "plans")


def _client_root() -> Path:
    """客户端仓库根：环境变量优先，默认同工作区兄弟目录。"""
    return Path(os.environ.get("XADMIN_CLIENT_DIR", REPO_ROOT.parent / "xadmin-client"))


def collect_violations(root: Path = REPO_ROOT, client_root: Path | None = None) -> list:
    """返回违例清单（空列表 = 通过）；``root`` / ``client_root`` 参数供测试注入。"""
    violations = []
    docs = root / "docs"
    main_readme = docs / "README.md"
    main_text = main_readme.read_text(encoding="utf-8") if main_readme.is_file() else ""
    for sub in INDEXED_DIRS:
        directory = docs / sub if sub else docs
        if not directory.is_dir():
            continue
        dir_readme = directory / "README.md"
        dir_text = dir_readme.read_text(encoding="utf-8") if sub and dir_readme.is_file() else ""
        for path in sorted(directory.glob("*.md")):
            if path.name == "README.md":
                continue
            if path.name in main_text or path.name in dir_text:
                continue
            violations.append(
                f"{path.relative_to(root).as_posix()}: 未被索引引用（登记到 docs/README.md 或同目录 README.md）"
            )
    client = client_root if client_root is not None else _client_root()
    client_docs = client / "docs"
    if client_docs.is_dir():
        client_readme = client_docs / "README.md"
        client_text = client_readme.read_text(encoding="utf-8") if client_readme.is_file() else ""
        for path in sorted(client_docs.glob("*.md")):
            if path.name == "README.md":
                continue
            if path.name not in client_text:
                violations.append(f"xadmin-client/docs/{path.name}: 未被 xadmin-client/docs/README.md 引用")
    return violations


def main() -> int:
    violations = collect_violations()
    if violations:
        print(f"文档索引覆盖：{len(violations)} 篇孤儿文档：")
        for item in violations:
            print(f"  - {item}")
        print("修复：把文档登记到索引（docs/README.md 或同目录 README.md 的表格/清单）后重跑。")
        return 1
    covered = " / ".join(f"docs/{sub}" for sub in INDEXED_DIRS if sub)
    print(f"文档索引覆盖通过：docs 根级与 {covered} 及 xadmin-client/docs 全部已登记。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
