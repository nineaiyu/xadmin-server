#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : check_tutorial_mirror
# author : ly_ix
# date : 2026/09/20
"""教程 ↔ 代码 镜像守护（教程中的命令 / 参数 / demo 路径必须与代码一致）。

三类检查（全部零语义分析、低成本零误报面）：

1. **demo 文件路径**：教程引用的 ``demo/**.py`` / ``src/views/demo/**`` 路径必须存在
   （客户端仓库缺失时跳过其路径；``XADMIN_DOCS_DIR`` 存在时同步校验文档站教程）；
2. **manage.py 子命令**：教程出现的 ``manage.py <cmd>`` 必须命中自定义命令或 Django 内置命令集；
3. **CLI 参数**：教程出现的 ``--xxx`` 必须命中某个自定义命令的参数集合（自动扫描
   ``**/management/commands`` 的 ``add_argument``）或工具参数白名单（pytest / pnpm / git 等）。

设计意图：不解析命令语义，只保证"教程里写出来的东西在当前代码里存在"——
命令改名 / 参数删除 / demo 目录调整时，教程不更新即 CI 失败。

用法::

    python scripts/check_tutorial_mirror.py   # 门禁（CI lint.yml）；违例退出码 1
"""

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 纳入校验的本仓库教程/手册（"当前状态"文档，不含 plans 台账与 ADR 历史）
TUTORIAL_DOCS = (
    "docs/guide/first-module-30min.md",
    "docs/guide/recipes.md",
    "docs/architecture/component-handbook.md",
    "docs/architecture/framework-cookbook.md",
    "docs/architecture/方案选型与对比.md",
)

# 文档站教程（跨仓，根缺失时跳过）
EXTERNAL_TUTORIAL_DOCS = (
    "example/new-app-api.md",
    "example/new-app-client.md",
    "example/new-app-menu.md",
    "example/new-app-test.md",
)

# Django 内置命令（教程允许出现；不做外部依赖注入，列常用集）
BUILTIN_COMMANDS = {
    "makemigrations",
    "migrate",
    "loaddata",
    "dumpdata",
    "startapp",
    "startproject",
    "shell",
    "check",
    "collectstatic",
    "compilemessages",
    "makemessages",
    "createsuperuser",
    "changepassword",
    "dbshell",
    "showmigrations",
    "sqlmigrate",
    "test",
    "runserver",
    "sendtestemail",
    "clearsessions",
}

# 工具类参数白名单（非 Django 命令：pytest / pnpm / git / 运维脚本等）
TOOL_PARAMS = {
    "--cov",
    "--cov-fail-under",
    "--cov-report",
    "--tb",
    "--exit-code",
    "--frozen-lockfile",
    "--filter",
    "--backend-only",
    "--update",
    "--append",
    "--print-major",
    "--check",
}

DEMO_SERVER_RE = re.compile(r"\b(demo/[a-zA-Z0-9_/]+\.py)\b")
DEMO_CLIENT_RE = re.compile(r"\b(src/views/demo/[a-zA-Z0-9_/]*)")
MANAGE_CMD_RE = re.compile(r"manage\.py\s+([a-z_][a-z0-9_]*)")
PARAM_RE = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")


def _docs_root() -> Path:
    """跨仓文档根（xadmin-docs）：环境变量优先，默认同工作区兄弟目录。"""
    return Path(os.environ.get("XADMIN_DOCS_DIR", REPO_ROOT.parent / "xadmin-docs"))


def _client_root() -> Path:
    """客户端仓库根（xadmin-client）：环境变量优先，默认同工作区兄弟目录。"""
    return Path(os.environ.get("XADMIN_CLIENT_DIR", REPO_ROOT.parent / "xadmin-client"))


def _command_params(root: Path) -> set:
    """扫描 management/commands 下全部 add_argument 的选项参数集合。"""
    params = set()
    for path in root.glob("**/management/commands/**/*.py"):
        path_str = str(path)
        if "/tests/" in path_str or ".venv" in path_str:
            continue
        text = path.read_text(encoding="utf-8")
        for matched in re.finditer(r"add_argument\(", text):
            window = text[matched.end() : matched.end() + 400]
            params.update(re.findall(r"""["'](--[a-z][a-z0-9-]*)["']""", window))
    return params


def _custom_commands(root: Path) -> set:
    """自定义管理命令名集合（单文件模块 + 包形式命令，跳过下划线私有模块）。"""
    names = set()
    for path in root.glob("**/management/commands/*.py"):
        if ".venv" in str(path):
            continue
        if path.name.startswith("_"):
            continue
        names.add(path.stem)
    for path in root.glob("**/management/commands/*/__init__.py"):
        if ".venv" in str(path):
            continue
        name = path.parent.name
        if not name.startswith("_"):
            names.add(name)
    return names


def collect_violations(root: Path = REPO_ROOT, client_root: Path | None = None, docs_root: Path | None = None) -> list:
    """返回违例清单（空列表 = 通过）；参数供测试注入。"""
    violations = []
    client = client_root if client_root is not None else _client_root()
    client_exists = client.is_dir()
    known_params = _command_params(root) | TOOL_PARAMS
    known_commands = _custom_commands(root) | BUILTIN_COMMANDS

    docs = []
    for rel in TUTORIAL_DOCS:
        docs.append((rel, root / rel))
    external_root = docs_root if docs_root is not None else _docs_root()
    if external_root.is_dir():
        for rel in EXTERNAL_TUTORIAL_DOCS:
            docs.append((f"xadmin-docs:{rel}", external_root / rel))

    for display, path in docs:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        # 1) demo 路径
        for pattern in (DEMO_SERVER_RE, DEMO_CLIENT_RE):
            for matched in pattern.finditer(text):
                candidate = matched.group(1)
                if (root / candidate).exists():
                    continue
                is_client_path = candidate.startswith("src/")
                if is_client_path and client_exists and (client / candidate).exists():
                    continue
                if is_client_path and not client_exists:
                    continue  # 单仓检出：客户端路径跳过
                violations.append(f"{display}: 引用的 demo 路径不存在：{candidate}")
        # 2) manage.py 子命令
        for matched in MANAGE_CMD_RE.finditer(text):
            command = matched.group(1)
            if command not in known_commands:
                violations.append(f"{display}: manage.py 子命令不存在：{command}")
        # 3) CLI 参数
        for matched in PARAM_RE.finditer(text):
            param = matched.group(1)
            if param not in known_params:
                violations.append(f"{display}: CLI 参数未在任何命令中定义：{param}")
    return violations


def main() -> int:
    violations = collect_violations()
    if violations:
        print(f"教程镜像校验：{len(violations)} 处与代码不一致：")
        for item in violations:
            print(f"  - {item}")
        print("修复：以代码为准更新教程（命令改名 / 参数调整 / demo 目录搬迁后同步文档）；")
        print("      若为工具类参数，登记到 scripts/check_tutorial_mirror.py 的 TOOL_PARAMS。")
        return 1
    print("教程镜像校验通过：命令 / 参数 / demo 路径与代码一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
