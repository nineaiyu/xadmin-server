# -*- coding: utf-8 -*-
"""工作区检出探测：四个 git 仓 + xadmin-web（非 git）的可用性登记。

约定与既有跨仓脚本一致：仓库根可用环境变量覆盖（``XADMIN_SERVER_DIR`` 等），
默认取工作区根下的同名兄弟目录。**缺仓 = missing（不是 pass）**——只有显式
``--allow-missing`` 才降级为 degraded。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# 仓 -> 判定「已检出」的标记文件（相对仓根）
REPO_MARKERS = {
    "xadmin-server": "server/const.py",
    "xadmin-client": "package.json",
    "xadmin-docs": "guide/demo.md",
    "xadmin-installer": "static.env",
    "xadmin-web": "default.conf",
}

# 仓 -> 路径覆盖环境变量（与既有 check_doc_facts.py / 前端 mjs 同约定）
REPO_ENV = {
    "xadmin-server": "XADMIN_SERVER_DIR",
    "xadmin-client": "XADMIN_CLIENT_DIR",
    "xadmin-docs": "XADMIN_DOCS_DIR",
    "xadmin-installer": "XADMIN_INSTALLER_DIR",
    "xadmin-web": "XADMIN_WEB_DIR",
}

GIT_REPOS = ("xadmin-server", "xadmin-client", "xadmin-docs", "xadmin-installer")
NON_GIT_REPOS = ("xadmin-web",)
ALL_REPOS = GIT_REPOS + NON_GIT_REPOS

# 默认放行：xadmin-web 不在任何 git 仓内，工作区/CI 通常不入检出
DEFAULT_ALLOW_MISSING = ("xadmin-web",)


@dataclass
class Discovered:
    root: Path
    paths: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    allowed_missing: set = field(default_factory=set)

    def path(self, repo: str) -> Path | None:
        """返回仓根路径；未检出返回 None。"""
        return self.paths.get(repo)

    def is_present(self, repo: str) -> bool:
        return self.paths.get(repo) is not None

    def is_allowed(self, repo: str) -> bool:
        return repo in self.allowed_missing

    def env(self, base=None) -> dict:
        """导出给子进程的跨仓环境变量（绝对路径，仅含已检出仓）。"""
        env = dict(os.environ if base is None else base)
        for repo, key in REPO_ENV.items():
            path = self.paths.get(repo)
            if path is not None:
                env[key] = str(path)
        return env


def _resolve_root(root: Path, repo: str) -> Path:
    override = os.environ.get(REPO_ENV[repo], "").strip()
    return Path(override) if override else Path(root) / repo


def detect_repo(root: Path, repo: str) -> Path | None:
    """按标记文件判定仓库是否已检出（存在且为目录 + 标记文件存在）。"""
    base = _resolve_root(root, repo)
    if base.is_dir() and (base / REPO_MARKERS[repo]).is_file():
        return base
    return None


def discover(root: Path, allow_missing=()) -> Discovered:
    """探测全部仓库；``allow_missing`` 为放行的缺失仓集合。"""
    root = Path(root)
    allowed = set(allow_missing)
    paths: dict = {}
    missing: list = []
    for repo in ALL_REPOS:
        detected = detect_repo(root, repo)
        paths[repo] = detected
        if detected is None:
            missing.append(repo)
    return Discovered(root=root, paths=paths, missing=missing, allowed_missing=allowed)


def missing_not_allowed(found: Discovered) -> list:
    """未被放行的缺失仓（存在即应使退出码为 2）。"""
    return [repo for repo in found.missing if repo not in found.allowed_missing]
