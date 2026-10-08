#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""内核分发包（xadmin-common）的版本校验 / 构建 / 发布通道。

口径（详见 docs/ops/kernel-release.md）：

- **版本单一事实源** = ``packages/xadmin-common/common/__init__.py`` 的 ``__version__``；
  成员 ``pyproject.toml`` 经 ``[tool.hatch.version]`` 从这里取值（不重复写版本号）；
- ``check``：版本四方一致性（源码 / 分发元数据 / changelog 首条 / 架构文档「当前」口径）+ 契约面就绪；
- ``build``：``uv build --package xadmin-common`` 出 wheel + sdist，并校验 wheel 内容
  （包本体 + ``templates/`` + ``migrations/`` 数据文件 + 元数据版本）与打印 sha256；
- ``publish``：上传私有源。**默认 dry-run**（只做校验与打印，不上传），加 ``--upload`` 才真上传；
  发布地址取 ``--url`` / ``--index`` 或环境变量（见下面命令示例），凭据经 uv 既有环境变量传递。

用法::

    python scripts/release_kernel.py check
    python scripts/release_kernel.py build [--out-dir dist]
    python scripts/release_kernel.py publish --url https://pypi.example.com/legacy/ [--upload]
    python scripts/release_kernel.py publish --index corp [--upload]      # uv 配置里的 index 名
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MEMBER_DIR = REPO_ROOT / "packages" / "xadmin-common"
INIT_FILE = MEMBER_DIR / "common" / "__init__.py"
CHANGELOG = MEMBER_DIR / "CHANGELOG.md"
PYPROJECT = MEMBER_DIR / "pyproject.toml"
KERNEL_DOC = REPO_ROOT / "docs" / "architecture" / "kernel-package.md"

DIST_NAME = "xadmin-common"
# 产物文件名按 PEP 503 把 `-` 规范化为 `_`（xadmin_common-0.2.0-py3-none-any.whl）
DIST_FILE_NAME = DIST_NAME.replace("-", "_")
PACKAGE_NAME = "common"

#: 发布地址相关环境变量（uv 原生变量之外的项目口径）
ENV_PUBLISH_URL = "XADMIN_COMMON_PUBLISH_URL"
ENV_PUBLISH_INDEX = "XADMIN_COMMON_INDEX"

_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)
_RELEASE_RE = re.compile(r"^##\s+\[(\d+\.\d+\.\d+)\]\s+-\s+(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)
_DOC_VERSION_RE = re.compile(r"当前\s+(\d+\.\d+\.\d+)")


def read_source_version() -> str:
    """读 ``common/__init__.py`` 的版本（单一事实源）。"""
    matched = _VERSION_RE.search(INIT_FILE.read_text(encoding="utf-8"))
    if not matched:
        raise SystemExit(f"未能在 {INIT_FILE} 解析 __version__（版本单一事实源缺失）")
    return matched.group(1)


def read_metadata_version() -> str:
    """读成员 pyproject 的版本声明形态（应为动态版本 + hatch 指向 __init__.py）。"""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    if project.get("version"):
        raise ValueError("成员 pyproject 不得重复声明 version（版本单一事实源是 common/__init__.py）")
    if "version" not in project.get("dynamic", []):
        raise ValueError('成员 pyproject 缺少 dynamic = ["version"]')
    hatch_path = (data.get("tool", {}).get("hatch", {}).get("version", {}) or {}).get("path")
    if hatch_path != "common/__init__.py":
        raise ValueError(f"成员 [tool.hatch.version].path 应指向 common/__init__.py，当前 {hatch_path!r}")
    return ""  # 动态取值，运行期由构建后端解析


def read_changelog_head() -> tuple[str, str]:
    """changelog 首个「已发布」条目 ``(版本, 日期)``（跳过 Unreleased）。"""
    matched = _RELEASE_RE.search(CHANGELOG.read_text(encoding="utf-8"))
    if not matched:
        raise SystemExit(f"{CHANGELOG} 缺少形如 `## [0.1.0] - YYYY-MM-DD` 的发布条目")
    return matched.group(1), matched.group(2)


def read_doc_version() -> str:
    """架构文档 §一 的「当前 X.Y.Z」口径。"""
    matched = _DOC_VERSION_RE.search(KERNEL_DOC.read_text(encoding="utf-8"))
    if not matched:
        raise SystemExit(f"{KERNEL_DOC} 缺少「当前 x.y.z」版本口径（发布渠道章节见 docs/ops/kernel-release.md）")
    return matched.group(1)


def contract_keys() -> tuple[int, int]:
    """契约面概览 ``(键总数, 必给键数)``（构建前确认契约模块可解析）。"""
    sys.path.insert(0, str(MEMBER_DIR))
    try:
        from common import settings_contract  # noqa: PLC0415  # 延迟导入：仅构建期校验用
    finally:
        sys.path.pop(0)
    total = len(settings_contract.KERNEL_SETTINGS)
    required = len(settings_contract.required_kernel_settings())
    return total, required


def check() -> int:
    """版本四方一致性 + 契约面就绪；返回退出码。"""
    problems: list[str] = []
    version = read_source_version()
    try:
        read_metadata_version()
        metadata = "动态取值（hatch → common/__init__.py）"
    except ValueError as exc:
        metadata = "配置错误"
        problems.append(str(exc))
    changelog_version, changelog_date = read_changelog_head()
    doc_version = read_doc_version()
    total, required = contract_keys()

    print(f"\n内核分发包 {DIST_NAME}（导入包 {PACKAGE_NAME}）")
    print(f"  源码版本（事实源）   : {version}  ({INIT_FILE.relative_to(REPO_ROOT)})")
    print(f"  分发元数据           : {metadata}  (packages/xadmin-common/pyproject.toml)")
    print(f"  changelog 首条       : {changelog_version}  ({changelog_date})")
    print(f"  架构文档「当前」口径 : {doc_version}  ({KERNEL_DOC.relative_to(REPO_ROOT)})")
    print(f"  settings 契约面      : {total} 键（必给 {required}）")

    if changelog_version != version:
        problems.append(f"changelog 首条 {changelog_version} 与源码版本 {version} 不一致（发版先登记本文件）")
    if doc_version != version:
        problems.append(f"架构文档「当前 {doc_version}」与源码版本 {version} 不一致")
    tag = f"{PACKAGE_NAME}-v{version}"
    if problems:
        print("\n一致性校验失败：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print(f"\n一致性校验通过。建议发布 tag：{tag}（发布动作见 docs/ops/kernel-release.md）")
    return 0


def _run(cmd: list[str]) -> None:
    print(f"\n$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        raise SystemExit(f"命令失败（退出码 {result.returncode}）：{' '.join(cmd)}")


def verify_wheel(wheel: Path, version: str) -> list[str]:
    """wheel 内容校验：包本体 + 数据文件 + 元数据版本。返回问题清单。"""
    problems: list[str] = []
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next((name for name in names if name.endswith(".dist-info/METADATA")), None)
        if metadata_name is None:
            problems.append("wheel 缺少 .dist-info/METADATA")
        else:
            metadata = archive.read(metadata_name).decode("utf-8")
            if f"Version: {version}" not in metadata:
                problems.append(f"wheel 元数据版本不是 {version}")
            if f"Name: {DIST_NAME}" not in metadata:
                problems.append(f"wheel 元数据分发名不是 {DIST_NAME}")
    for required in (f"{PACKAGE_NAME}/__init__.py", f"{PACKAGE_NAME}/settings_contract.py", f"{PACKAGE_NAME}/apps.py"):
        if required not in names:
            problems.append(f"wheel 缺少 {required}")
    for data_dir in (f"{PACKAGE_NAME}/templates/", f"{PACKAGE_NAME}/migrations/"):
        if not any(name.startswith(data_dir) and not name.endswith("/") for name in names):
            problems.append(f"wheel 缺少数据文件目录 {data_dir}")
    if any(name.startswith("server/") or name.startswith("system/") for name in names):
        problems.append("wheel 混入了宿主工程/业务 app 内容（只应包含内核包本体）")
    return problems


def build(out_dir: Path) -> int:
    """构建 wheel + sdist 并校验产物内容。"""
    version = read_source_version()
    _run(["uv", "build", "--package", DIST_NAME, "--out-dir", str(out_dir)])
    wheels = sorted(out_dir.glob(f"{DIST_FILE_NAME}-{version}-*.whl"))
    sdists = sorted(out_dir.glob(f"{DIST_FILE_NAME}-{version}.tar.gz"))
    if not wheels or not sdists:
        print(f"\n未找到 {version} 的构建产物：wheel={wheels} sdist={sdists}")
        return 1
    problems = verify_wheel(wheels[-1], version)
    print(f"\n产物校验：{wheels[-1].name}")
    for artifact in (*wheels, *sdists):
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        print(f"  {artifact.name}\n    sha256={digest}\n    size={artifact.stat().st_size} bytes")
    if problems:
        print("\n产物校验失败：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("\nwheel 内容与元数据校验通过（包本体 + templates/ + migrations/，无误带宿主内容）")
    return 0


def publish(out_dir: Path, url: str | None, index: str | None, upload: bool) -> int:
    """按配置上传（默认 dry-run）。"""
    version = read_source_version()
    wheels = sorted(out_dir.glob(f"{DIST_FILE_NAME}-{version}-*.whl"))
    sdists = sorted(out_dir.glob(f"{DIST_FILE_NAME}-{version}.tar.gz"))
    if not wheels:
        print(f"\n{dist_hint(out_dir, version)}")
        return 1
    if not url and not index:
        print(
            f"\n未提供发布地址：用 --url / --index，或设置环境变量 {ENV_PUBLISH_URL} / {ENV_PUBLISH_INDEX}\n"
            f"（渠道形态与凭据口径见 docs/ops/kernel-release.md §一）"
        )
        return 1
    cmd = ["uv", "publish", *[str(item) for item in (*wheels, *sdists)]]
    if url:
        cmd += ["--publish-url", url, "--check-url", url]
    if index:
        cmd += ["--index", index]
    if not upload:
        cmd.append("--dry-run")
    _run(cmd)
    if not upload:
        print("\ndry-run 完成（未上传）。确认无误后加 --upload 真上传；凭据经 uv 环境变量传递（如 UV_PUBLISH_TOKEN）")
    else:
        print(f"\n已上传 {DIST_NAME} {version}；宿主升级口径见 docs/ops/kernel-release.md §四")
    return 0


def dist_hint(out_dir: Path, version: str) -> str:
    return f"未找到 {DIST_NAME} {version} 的 wheel（{out_dir}）：请先执行 python scripts/release_kernel.py build"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="内核分发包（xadmin-common）版本校验 / 构建 / 发布")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="版本四方一致性 + 契约面就绪校验")

    build_parser = sub.add_parser("build", help="构建 wheel + sdist 并校验产物内容")
    build_parser.add_argument("--out-dir", default="dist", help="产物目录（相对仓库根，默认 dist）")

    publish_parser = sub.add_parser("publish", help="上传私有源（默认 dry-run，--upload 才真上传）")
    publish_parser.add_argument("--out-dir", default="dist", help="产物目录（默认 dist）")
    publish_parser.add_argument("--url", default=os.environ.get(ENV_PUBLISH_URL) or None, help="上传端点（legacy API）")
    publish_parser.add_argument(
        "--index", default=os.environ.get(ENV_PUBLISH_INDEX) or None, help="uv 配置中的 index 名"
    )
    publish_parser.add_argument("--upload", action="store_true", help="真上传（默认只做 dry-run）")

    args = parser.parse_args(argv)
    if args.command in {"build", "publish"} and shutil.which("uv") is None:
        print("未找到 uv（构建 / 发布需要 https://docs.astral.sh/uv/）")
        return 1
    if args.command == "check":
        return check()
    if args.command == "build":
        return build(Path(args.out_dir))
    return publish(Path(args.out_dir), args.url, args.index, args.upload)


if __name__ == "__main__":
    sys.exit(main())
