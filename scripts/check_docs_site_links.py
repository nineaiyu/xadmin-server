#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : check_docs_site_links
# author : ly_ix
# date : 2026/10/16
"""文档站链接与引用可达性守护（锚点级死链 + 服务端路径引用）。

背景：``xadmin-docs`` 的 ``docs:build`` 只能拦**页级**死链——实测（vitepress 1.6.4）：

- ``](./no-such-page.md)`` → 构建 exit 1（已覆盖）；
- ``](./page.md#不存在的章节)`` → 构建 exit 0（**未覆盖**，章节被改写/删除后锚点静默失效）。

故本脚本补两件事：

1. **站内链接**：目标页必须存在；带 ``#fragment`` 时锚点必须出现在构建产物
   （``dist/**/*.html``）的 ``id="..."`` 集合里——以产物为准，不重实现 vitepress 的 slug
   规则（中文、箭头、全角标点的归一化行为与直觉不同，手写规则必然漂移）。
2. **服务端路径引用**：正文反引号里引用的服务端仓库路径（``docs/`` / ``ops/`` / ``scripts/``
   前缀且带已知扩展名）必须可达——手册改名 / 移动后引用不更新即失败。

跳过口径（宁可漏报不误报）：跨仓文档根缺失（单仓检出）→ 整体跳过；构建产物缺失（本地未
构建）→ 只做页级存在性校验，并明确打印「锚点校验已跳过」而非静默通过；代码围栏（``` / ~~~）
内的链接与路径引用不参与校验（围栏内不渲染成链接）。

用法::

    python scripts/check_docs_site_links.py                    # 自动探测 ../xadmin-docs 与 dist
    python scripts/check_docs_site_links.py --dist ../xadmin-docs/dist
    python scripts/check_docs_site_links.py --require-dist      # 构建产物缺失即失败（CI 用）
"""

import os
import posixpath
import re
import sys
from html import unescape
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parent.parent

# 站点内容目录（与 check_docs_site_nav.py 同口径）；其余目录的 md 不参与校验
SITE_DIRS = ("guide", "advanced", "example", "problem", "devguidelines")
SKIP_DIRS = {"node_modules", "dist", ".vitepress", ".git", ".github", "public"}
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+?)(?:\s+\"[^\"]*\")?\)")
ID_RE = re.compile(r'\sid="([^"]*)"')
CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
# 服务端路径引用：只在已知顶层目录 + 已知扩展名下校验（避免把正文里的普通词当路径）。
# ``scripts/`` 前缀两个仓都有同名目录（安装器脚本 vs server 门禁脚本），任一侧命中即放过。
PATH_PREFIXES = {
    "docs/": ("server",),
    "ops/": ("server",),
    "scripts/": ("installer", "server"),
}
PATH_EXTENSIONS = (".md", ".py", ".sh", ".json", ".yml", ".yaml", ".ts", ".vue", ".mts")
EXTERNAL_SCHEMES = ("http://", "https://", "mailto:", "tel:", "ftp:", "data:", "//")


def _docs_root() -> Path:
    """跨仓文档根（xadmin-docs）：环境变量优先，默认同工作区兄弟目录。"""
    return Path(os.environ.get("XADMIN_DOCS_DIR", REPO_ROOT.parent / "xadmin-docs"))


def _installer_root() -> Path:
    """安装器仓根（``scripts/*.sh`` 的归属仓）：缺失时该前缀跳过（单仓检出守卫）。"""
    return Path(os.environ.get("XADMIN_INSTALLER_DIR", REPO_ROOT.parent / "xadmin-installer"))


def _page_files(docs_root: Path) -> list:
    """站点内容页面：根级 *.md + 内容目录 *.md（排除构建产物与依赖目录）。"""
    pages = []
    for path in sorted(docs_root.rglob("*.md")):
        rel = path.relative_to(docs_root)
        if set(rel.parts) & SKIP_DIRS:
            continue
        if len(rel.parts) > 1 and rel.parts[0] not in SITE_DIRS:
            continue
        pages.append(path)
    return pages


def _strip_fences(text: str) -> list:
    """返回逐行文本，围栏代码块内的行替换为空串（保持行号对齐）。"""
    lines = text.splitlines()
    inside = False
    result = []
    for line in lines:
        if FENCE_RE.match(line):
            inside = not inside
            result.append("")
            continue
        result.append("" if inside else line)
    return result


def _resolve_page(target: str, page: Path, docs_root: Path):
    """把站内链接目标解析为 markdown 文件路径；站外 / 非页面目标返回 None。"""
    if not target or target.startswith(EXTERNAL_SCHEMES):
        return None
    base = docs_root if target.startswith("/") else page.parent
    rel = target.lstrip("/").split("?")[0]
    if rel.endswith(".md"):
        name = rel
    elif rel.endswith(".html"):
        stem = rel[: -len(".html")]
        name = (stem[: -len("/index")] + ".md") if stem.endswith("/index") else stem + ".md"
    elif rel == "" or rel.endswith("/"):
        name = rel + "index.md"
    elif posixpath.splitext(rel)[1]:
        name = rel  # 资源引用（图片 / 下载文件）：按原路径校验存在性
    else:
        name = rel + ".md"
    candidate = Path(posixpath.normpath((base / name).as_posix()))
    if not candidate.is_file() and target.startswith("/"):
        # 根相对资源由 vitepress 从 public/ 提供（构建时拷到站点根）
        candidate = Path(posixpath.normpath((docs_root / "public" / name).as_posix()))
    return candidate


def _dist_page(md_path: Path, docs_root: Path, dist_root: Path):
    """页面 md → 构建产物 html（页面不在站点内时返回 None）。"""
    try:
        rel = md_path.relative_to(docs_root)
    except ValueError:
        return None
    target = dist_root / rel.with_suffix(".html")
    if rel.name == "index.md":
        target = dist_root / rel.parent / "index.html"
    return target


def _page_ids(dist_page: Path):
    """构建产物里的锚点 id 集合；产物缺失返回 None（调用方跳过锚点校验）。"""
    if not dist_page.is_file():
        return None
    text = dist_page.read_text(encoding="utf-8", errors="ignore")
    return {unescape(unquote(raw)) for raw in ID_RE.findall(text)}


def _path_candidate_roots(token: str, roots: dict) -> list:
    """反引号路径引用的候选仓库根（前缀 → 归属仓）；不是路径引用时返回空列表。

    只要有一个归属仓未检出（单仓检出 / CI 未 checkout）就跳过：无法判定即不误报。
    """
    for prefix, owners in PATH_PREFIXES.items():
        if not token.startswith(prefix) or not token.endswith(PATH_EXTENSIONS):
            continue
        if any(char in token for char in " <>{*}|…\"'()，。"):
            return []
        if any(roots.get(name) is None for name in owners):
            return []
        return [roots[name] for name in owners]
    return []


def collect_violations(docs_root=None, dist_root=None, server_root=None, installer_root=None) -> list:
    """返回违例清单（空列表 = 通过）；各根参数供测试注入。"""
    docs = docs_root if docs_root is not None else _docs_root()
    if not docs.is_dir():
        return []  # 跨仓未检出：跳过而非误报
    dist = dist_root if dist_root is not None else docs / "dist"
    installer = installer_root if installer_root is not None else _installer_root()
    roots = {
        "server": server_root if server_root is not None else REPO_ROOT,
        "installer": installer,
    }
    roots = {name: path if path.is_dir() else None for name, path in roots.items()}
    violations = []
    for page in _page_files(docs):
        rel = page.relative_to(docs).as_posix()
        display = f"xadmin-docs:{rel}"
        lines = _strip_fences(page.read_text(encoding="utf-8"))
        for line_no, line in enumerate(lines, start=1):
            for raw_link in LINK_RE.findall(line):
                link, _, fragment = raw_link.partition("#")
                fragment = unquote(fragment)
                target_page = _resolve_page(link, page, docs) if link else page
                if target_page is None:
                    continue
                if not target_page.is_file():
                    violations.append(f"{display}:{line_no}: 链接目标不存在 → {raw_link}")
                    continue
                if not fragment or target_page.suffix != ".md":
                    continue  # 资源引用无锚点语义
                ids = _page_ids(_dist_page(target_page, docs, dist))
                if ids is None or fragment in ids:
                    continue
                violations.append(f"{display}:{line_no}: 锚点不存在（{raw_link}）—— 章节已改名或删除")
            for token in CODE_SPAN_RE.findall(line):
                candidates = _path_candidate_roots(token, roots)
                if not candidates:
                    continue
                if not any((root / token).exists() for root in candidates):
                    violations.append(f"{display}:{line_no}: 服务端路径引用不存在 → `{token}`")
    return violations


def main() -> int:
    docs = _docs_root()
    dist = docs / "dist"
    require_dist = "--require-dist" in sys.argv
    if "--dist" in sys.argv:
        dist = Path(sys.argv[sys.argv.index("--dist") + 1])
        require_dist = True
    violations = collect_violations(dist_root=dist)
    if docs.is_dir() and not dist.is_dir():
        if require_dist:
            print(f"文档站链接校验：构建产物缺失 → {dist}（先执行 pnpm docs:build）")
            return 1
        print(f"提示：构建产物 {dist} 不存在，锚点级校验已跳过（只校验页级存在性）。")
    if violations:
        print(f"文档站链接校验：{len(violations)} 条违例：")
        for item in violations:
            print(f"  - {item}")
        print("修复：更新链接 / 锚点，或同步服务端路径引用后重跑（锚点以 pnpm docs:build 的产物为准）。")
        return 1
    print("文档站链接校验通过：站内链接与锚点、服务端路径引用均可达。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
