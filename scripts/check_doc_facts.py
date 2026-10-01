#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : check_doc_facts
# author : ly_ix
# date : 2026/09/19
"""文档事实校验（单一事实源防漂移，G4）。

口径：**代码是事实源，文档描述"当前状态"的版本串必须与代码一致**。
事实源：

- ``requirements.txt``（依赖版本；``requirements:<pkg>`` 全版本 / ``requirements_minor:<pkg>`` 主次版本）；
- ``server/const.py`` 的 ``VERSION``（平台版本，``const:VERSION``）；
- ``.github/workflows/lint.yml`` 的 python-version（``workflow:python``）；
- ``.github/workflows/test.yml`` 的覆盖率门禁（``workflow:cov_fail_under``）；
- 客户端仓库 ``package.json`` 的 engines（``client_engine:node`` / ``client_engine:pnpm``，跨仓）。

跨仓事实：``"external": True`` = 文档位于 xadmin-docs 仓库（根由 ``XADMIN_DOCS_DIR`` 指定，
默认 ``../xadmin-docs``）；``"external": "client"`` = 文档位于客户端仓库（``XADMIN_CLIENT_DIR``，
默认 ``../xadmin-client``）。**跨仓根不存在时跳过**（单仓检出守卫，与 client 侧
``XADMIN_SERVER_DIR`` 口径一致）——文档站仓库 CI 由 docs-build.yml checkout 两仓后调用；
客户端仓库 CI（lint-code.yml）已 checkout 本仓库，可就地守护 client 侧文档事实与索引。

校验面只圈定「当前事实」文档（``FACTS`` 表逐条登记），不扫描全文：
历史类内容（ADR 决策记录 / plans 台账 / metrics 履历 / 归档）里的旧版本串是**当时的事实**，
不属于漂移；"X.Y.Z 起"这类追溯性表述跨版本仍然为真，也不校验。

用法::

    python scripts/check_doc_facts.py   # 门禁（CI lint.yml / docs-build.yml）；违例退出码 1

新增受保护事实：往 ``FACTS`` 登记一条（文档 / 标签 / 单捕获组正则 / 事实源）。
"""

import json
import os
import re
import sys
from pathlib import Path

# 同目录模块（doc_facts_rules）解析：脚本直跑时 sys.path[0] 即本目录；
# 测试经 importlib 按路径加载本文件时需显式补入，才能解析同目录导入
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from doc_facts_rules import FACTS

REPO_ROOT = Path(__file__).resolve().parent.parent


def _docs_root() -> Path:
    """跨仓文档根（xadmin-docs）：环境变量优先，默认同工作区兄弟目录。"""
    return Path(os.environ.get("XADMIN_DOCS_DIR", REPO_ROOT.parent / "xadmin-docs"))


def _client_root() -> Path:
    """客户端仓库根（xadmin-client）：环境变量优先，默认同工作区兄弟目录。"""
    return Path(os.environ.get("XADMIN_CLIENT_DIR", REPO_ROOT.parent / "xadmin-client"))


# 受保护事实：文档中的「当前状态」断言 ↔ 代码事实源
# doc      文档路径（external=True 时相对 XADMIN_DOCS_DIR，否则相对本仓库根）；
# label    事实名（输出用）；
# pattern  单捕获组正则（文档内全部命中都必须等于事实源的值；零命中也算失败，
#          防止文档改版后受保护事实被悄悄丢掉）；
# source   事实源："requirements:<pkg>" / "requirements_minor:<pkg>" / "const:VERSION" /
#          "workflow:python" / "workflow:cov_fail_under" / "client_engine:<node|pnpm>"；
# external 可选：True = 文档位于 xadmin-docs 仓库；"client" = 文档位于客户端仓库（跨仓，根缺失时跳过）。
def _requirements_versions() -> dict:
    """requirements.txt 的 ``包名(小写) → 版本``（跳过注释与范围声明）。"""
    versions = {}
    for line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, _, version = line.partition("==")
        # 导出产物可能带平台 marker（``1.2.3 ; sys_platform == 'win32'``），版本取分号前段
        versions[name.strip().lower()] = version.split(";", 1)[0].strip()
    return versions


def _const_version() -> str:
    text = (REPO_ROOT / "server" / "const.py").read_text(encoding="utf-8")
    matched = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if not matched:
        raise SystemExit(f"无法从 server/const.py 解析 VERSION：{REPO_ROOT / 'server' / 'const.py'}")
    return matched.group(1)


def _workflow_value(key: str):
    """从 CI workflow 读取事实（``python`` / ``cov_fail_under``）；解析失败返回 None。"""
    if key == "python":
        path = REPO_ROOT / ".github" / "workflows" / "lint.yml"
        matched = re.search(r'python-version:\s*"([\d.]+)"', path.read_text(encoding="utf-8"))
    elif key == "cov_fail_under":
        path = REPO_ROOT / ".github" / "workflows" / "test.yml"
        matched = re.search(r"--cov-fail-under=(\d+)", path.read_text(encoding="utf-8"))
    else:
        raise SystemExit(f"未知 workflow 事实键：{key}（支持 python / cov_fail_under）")
    return matched.group(1) if matched else None


def _adr_count() -> str:
    """ADR 文件数事实源：``docs/adr/ADR-*.md``（不含索引 README）。

    索引里的「N 篇」是人工维护的计数，历史上漂移过（写 53、实际 66）——
    改由文件系统直接计数，新增 ADR 忘记改索引即失败。
    """
    return str(len(list((REPO_ROOT / "docs" / "adr").glob("ADR-*.md"))))


def _client_engine(key: str):
    """客户端 package.json 的 engines.<key>（跨仓；仓库缺失或解析失败返回 None）。"""
    package = _client_root() / "package.json"
    if not package.is_file():
        return None
    data = json.loads(package.read_text(encoding="utf-8"))
    raw = str(data.get("engines", {}).get(key, ""))
    matched = re.search(r"([\d.]+)", raw)
    return matched.group(1) if matched else None


def _resolve_source(source: str):
    """返回事实值；跨仓事实源不可用（目录缺失/解析失败）时返回 None（调用方跳过）。"""
    kind, _, key = source.partition(":")
    if kind == "requirements":
        versions = _requirements_versions()
        if key not in versions:
            raise SystemExit(f"requirements.txt 中未找到 {key}（事实源缺失，请核对 FACTS）")
        return versions[key]
    if kind == "requirements_minor":
        versions = _requirements_versions()
        if key not in versions:
            raise SystemExit(f"requirements.txt 中未找到 {key}（事实源缺失，请核对 FACTS）")
        return ".".join(versions[key].split(".")[:2])
    if kind == "const" and key == "VERSION":
        return _const_version()
    if kind == "workflow":
        return _workflow_value(key)
    if kind == "client_engine":
        return _client_engine(key)
    if kind == "adr" and key == "count":
        return _adr_count()
    raise SystemExit(
        f"未知事实源：{source}（支持 requirements[:minor] / const:VERSION / workflow / client_engine / adr:count）"
    )


def collect_violations(
    root: Path = REPO_ROOT,
    facts=None,
    docs_root: Path | None = None,
    client_root: Path | None = None,
) -> list:
    """返回违例清单（空列表 = 通过）；``facts`` / ``docs_root`` / ``client_root`` 参数供测试注入。

    跨仓事实：``external: True`` 从 ``docs_root``（默认 ``XADMIN_DOCS_DIR`` / ``../xadmin-docs``）解析，
    ``external: "client"`` 从客户端仓库（``client_root``，默认 ``../xadmin-client``）解析；
    跨仓根不存在或事实源暂不可用时跳过（单仓检出守卫）。
    """
    violations = []
    external_root = docs_root if docs_root is not None else _docs_root()
    client = client_root if client_root is not None else _client_root()
    for fact in facts if facts is not None else FACTS:
        external = fact.get("external")
        if external == "client":
            if not client.is_dir():
                continue
            doc = client / fact["doc"]
            display = f"xadmin-client:{fact['doc']}"
        elif external:
            if not external_root.is_dir():
                continue
            doc = external_root / fact["doc"]
            display = f"xadmin-docs:{fact['doc']}"
        else:
            doc = root / fact["doc"]
            display = fact["doc"]
        if not doc.is_file():
            violations.append(f"{display}: 文档不存在（受保护事实 {fact['label']} 失去载体）")
            continue
        expected = _resolve_source(fact["source"])
        if expected is None:
            continue
        text = doc.read_text(encoding="utf-8")
        matches = list(re.finditer(fact["pattern"], text))
        if not matches:
            violations.append(
                f"{display}: 未匹配到受保护事实「{fact['label']}」"
                f"（正则 {fact['pattern']}；文档改版后请同步更新 FACTS）"
            )
            continue
        for matched in matches:
            actual = matched.group(1)
            if actual != expected:
                line_no = text.count("\n", 0, matched.start()) + 1
                violations.append(
                    f"{display}:{line_no}: 「{fact['label']}」文档写 {actual}，"
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
        print("修复：以代码事实源（requirements.txt / server/const.py / workflows / client package.json）")
        print("      为准更新文档；若为文档结构变化，同步维护 scripts/check_doc_facts.py 的 FACTS 表。")
        return 1
    print(f"文档事实校验通过：{checked} 条受保护事实与代码一致（跨仓缺失时自动跳过）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
