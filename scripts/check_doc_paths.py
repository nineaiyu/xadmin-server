#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : check_handbook_sources
# author : ly_ix
# date : 2026/09/20
"""文档路径可达性守护（组件手册 + 活跃开发文档）。

校验范围：

1. **组件手册**（``docs/architecture/component-handbook.md``）：
   - **权威源行**（宽松）：含「权威源」的行内反引号标注路径必须可达——"改组件先改哪"的
     锚点，指向不存在的路径 = 文档已失真；
   - **全文顶层路径**（严格）：以已知仓库顶层目录开头的引用（``common/`` / ``src/`` /
     ``notifications/`` …，含 ``xadmin-client/`` 跨仓标识）必须可达；
2. **活跃开发文档**（``docs/architecture/*.md`` / ``docs/guide/*.md`` / ``docs/*.md`` 根级，
   排除履历性质的 ``docs/metrics.md``）：**代码路径引用**（``common/`` / ``system/`` /
   ``server/`` / ``notifications/`` / ``settings/`` / ``message/`` / ``mfa/`` / ``captcha/`` /
   ``demo/`` 前缀）必须可达——文件拆包 / 改名（如 ``modules.py`` → ``modules/``）后
   引用不更新即 CI 失败。

设计意图：文档引用的路径必须真实存在；统一写"仓库根相对完整路径"后，示例性简写
（``crm/models.py``）与占位符（``<name>`` / ``*`` / ``{a,b}`` 模板）自动豁免。

查找顺序：server 仓库根 → 客户端仓库根（``XADMIN_CLIENT_DIR``，默认 ``../xadmin-client``）→
文档站根（``XADMIN_DOCS_DIR``，默认 ``../xadmin-docs``）；跨仓未检出时其专属路径跳过。
``文件::符号`` 与 ``文件:行号`` 形式只校验文件部分；``{a,b,c}`` 花括号展开。

用法::

    python scripts/check_doc_paths.py   # 门禁（CI lint.yml / docs-build.yml / lint-code.yml）；违例退出码 1
"""

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

HANDBOOK = "docs/architecture/component-handbook.md"
TOKEN_RE = re.compile(r"`([^`\n]+)`")
BRACE_RE = re.compile(r"\{([^{}]+)\}")
CLIENT_ONLY_PREFIXES = ("src/", "locales/", "e2e/", "contract/")
# 全文路径校验：以已知仓库顶层目录开头（server 侧）
SERVER_TOP_PREFIXES = (
    "common/",
    "system/",
    "server/",
    "notifications/",
    "settings/",
    "message/",
    "mfa/",
    "captcha/",
    "loadjson/",
    "scripts/",
    "utils/",
    "docs/",
    "tests/",
    "demo/",
    "schema/",
)
# 跨仓标识：xadmin-client/xxx → 客户端仓库的 xxx；xadmin-docs/xxx → 文档站仓库的 xxx
CROSS_REPO_PREFIXES = {"xadmin-client/": "client", "xadmin-docs/": "docs"}
# 活跃开发文档（校验范围 2）：glob 集 + 排除项（履历性质文档保留历史路径）
ACTIVE_DOC_GLOBS = ("docs/architecture/*.md", "docs/guide/*.md", "docs/*.md")
EXCLUDE_ACTIVE_DOCS = ("docs/metrics.md",)
# 活跃文档只校验"代码路径"（窄前缀集；示例性路径多集中于 loadjson / utils / tests 等，不纳入）
CODE_PREFIXES = (
    "common/",
    "system/",
    "server/",
    "notifications/",
    "settings/",
    "message/",
    "mfa/",
    "captcha/",
    "demo/",
)


def _client_root() -> Path:
    """客户端仓库根：环境变量优先，默认同工作区兄弟目录。"""
    return Path(os.environ.get("XADMIN_CLIENT_DIR", REPO_ROOT.parent / "xadmin-client"))


def _docs_root() -> Path:
    """文档站仓库根：环境变量优先，默认同工作区兄弟目录。"""
    return Path(os.environ.get("XADMIN_DOCS_DIR", REPO_ROOT.parent / "xadmin-docs"))


def _looks_like_path(token: str) -> bool:
    """是否为"像仓库路径"的 token（用于过滤权威源行内的说明文字）。"""
    if not token or " " in token:
        return False
    if any("\u4e00" <= ch <= "\u9fff" for ch in token):
        return False
    if token.startswith(("http", "#", "~", "-")) or "=" in token or ":" in token:
        return False
    if any(ch in token for ch in "()（）"):
        return False
    return "/" in token or bool(re.search(r"\.[a-zA-Z0-9]+$", token))


def _expand_braces(token: str) -> list:
    """展开 ``{a,b,c}``（单层，手册实际用法足够）。"""
    matched = BRACE_RE.search(token)
    if not matched:
        return [token]
    prefix, suffix = token[: matched.start()], token[matched.end() :]
    return [f"{prefix}{item.strip()}{suffix}" for item in matched.group(1).split(",")]


def _handbook_paths(text: str) -> list:
    """提取所有含「权威源」的行里的候选路径（去重保持顺序）。"""
    paths = []
    for line in text.splitlines():
        if "权威源" not in line:
            continue
        for token in TOKEN_RE.findall(line):
            token = token.strip()
            if not _looks_like_path(token):
                continue
            paths.extend(_expand_braces(token))
    result = []
    seen = set()
    for candidate in paths:
        if candidate not in seen:
            seen.add(candidate)
            result.append(candidate)
    return result


def _dedupe(paths: list) -> list:
    """去重（保持出现顺序）。"""
    result = []
    seen = set()
    for candidate in paths:
        if candidate not in seen:
            seen.add(candidate)
            result.append(candidate)
    return result


def _top_level_paths(text: str) -> list:
    """全文提取"以已知仓库顶层目录开头"的反引号路径（示例简写 / 占位符自动豁免）。"""
    paths = []
    for raw in TOKEN_RE.findall(text):
        # "文件::符号" 写法（如 common/base/utils.py::signer）只校验文件部分
        token = raw.split("::")[0].strip()
        if not token or " " in token:
            continue
        if any(ch in token for ch in "<>*'\"") or any("\u4e00" <= ch <= "\u9fff" for ch in token):
            continue
        prefixes = SERVER_TOP_PREFIXES + CLIENT_ONLY_PREFIXES
        if not (token.startswith(prefixes) or token.startswith(tuple(CROSS_REPO_PREFIXES))):
            continue
        paths.extend(_expand_braces(token))
    return _dedupe(paths)


def _code_path_rows(text: str) -> list:
    """活跃文档的"代码路径"引用（窄前缀集；``::`` / ``:行号`` 只取文件部分）。"""
    paths = []
    for raw in TOKEN_RE.findall(text):
        token = re.sub(r"::.*$", "", raw)
        token = re.sub(r":\d+(-\d+)?$", "", token).strip()
        if not token or " " in token:
            continue
        if any(ch in token for ch in "<>*'\"{}") or any("\u4e00" <= ch <= "\u9fff" for ch in token):
            continue
        if not token.startswith(CODE_PREFIXES):
            continue
        paths.extend(_expand_braces(token))
    return _dedupe(paths)


def _verify(candidate: str, root: Path, client: Path, docs: Path, client_exists: bool, docs_exists: bool):
    """路径可达性三态：True 可达 / False 失效 / None 跨仓未检出（跳过）。"""
    if candidate.startswith("xadmin-client/"):
        return None if not client_exists else (client / candidate[len("xadmin-client/") :]).exists()
    if candidate.startswith("xadmin-docs/"):
        return None if not docs_exists else (docs / candidate[len("xadmin-docs/") :]).exists()
    if candidate.startswith(CLIENT_ONLY_PREFIXES):
        return None if not client_exists else (client / candidate).exists()
    if (root / candidate).exists():
        return True
    if client_exists and (client / candidate).exists():
        return True
    return False


def collect_violations(
    root: Path = REPO_ROOT,
    client_root: Path | None = None,
    docs_root: Path | None = None,
) -> list:
    """返回违例清单（空列表 = 通过）；三个根参数供测试注入。"""
    client = client_root if client_root is not None else _client_root()
    docs = docs_root if docs_root is not None else _docs_root()
    client_exists, docs_exists = client.is_dir(), docs.is_dir()
    violations = []
    handbook = root / HANDBOOK
    if handbook.is_file():
        text = handbook.read_text(encoding="utf-8")
        # 权威源行 + 全文顶层路径合并去重后校验（同一路径只报一次）
        for candidate in _dedupe(_handbook_paths(text) + _top_level_paths(text)):
            if _verify(candidate, root, client, docs, client_exists, docs_exists) is False:
                violations.append(f"{HANDBOOK}: 引用的路径不存在：{candidate}")
    else:
        violations.append(f"{HANDBOOK}: 文档不存在（路径校验失去载体）")
    for pattern in ACTIVE_DOC_GLOBS:
        for doc in sorted(root.glob(pattern)):
            rel = doc.relative_to(root).as_posix()
            if rel in EXCLUDE_ACTIVE_DOCS or rel == HANDBOOK:
                continue
            for candidate in _code_path_rows(doc.read_text(encoding="utf-8")):
                if _verify(candidate, root, client, docs, client_exists, docs_exists) is False:
                    violations.append(f"{rel}: 引用的代码路径不存在：{candidate}")
    return violations


def main() -> int:
    violations = collect_violations()
    if violations:
        print(f"文档路径校验：{len(violations)} 处引用失效：")
        for item in violations:
            print(f"  - {item}")
        print("修复：文档引用的路径须真实可达（统一写仓库根相对完整路径）；")
        print("      文件拆包 / 改名后同步更新引用，或纠正笔误。")
        return 1
    print("文档路径校验通过：组件手册与活跃开发文档的引用路径全部可达。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
