#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""验证码资产门禁：manifest 许可 / 来源 / 哈希 / 路径 / 体积。

资产只允许随包或只读挂载；本门禁防止无许可、路径穿越、哈希漂移或超大文件
进入镜像。manifest 的 ``files`` 为空时视为程序化兜底，合法。
"""

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "captcha" / "resources" / "backgrounds"
ALLOWED_LICENSES = {"CC0", "OFL", "Apache-2.0", "MIT", "procedural", "self"}
ALLOWED_SUFFIXES = {".webp", ".jpg", ".jpeg", ".png"}
MAX_BYTES = 2 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_root(root: Path) -> list[str]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return [f"{root}: 缺少 manifest.json"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{manifest_path}: 无法解析：{exc}"]
    problems: list[str] = []
    license_name = str(manifest.get("license") or "")
    if license_name not in ALLOWED_LICENSES:
        problems.append(f"{manifest_path}: 许可不在白名单（{license_name or '<empty>'}）")
    if not str(manifest.get("source") or "").strip():
        problems.append(f"{manifest_path}: 缺少 source")
    for item in manifest.get("files") or []:
        if not isinstance(item, dict):
            problems.append(f"{manifest_path}: files 项必须是对象")
            continue
        raw = str(item.get("file") or "").strip()
        path = Path(raw)
        if not raw or path.is_absolute() or ".." in path.parts:
            problems.append(f"{manifest_path}: 非法文件路径 {raw!r}")
            continue
        target = (root / path).resolve()
        if not target.is_relative_to(root.resolve()):
            problems.append(f"{manifest_path}: 路径越界 {raw}")
            continue
        if target.suffix.lower() not in ALLOWED_SUFFIXES:
            problems.append(f"{manifest_path}: 不允许的扩展名 {target.suffix}")
            continue
        if not target.is_file():
            problems.append(f"{manifest_path}: 文件不存在 {raw}")
            continue
        if target.stat().st_size > MAX_BYTES:
            problems.append(f"{manifest_path}: 文件超过 2MB {raw}")
        expected = str(item.get("sha256") or "").strip().lower()
        if not expected or not expected == _sha256(target):
            problems.append(f"{manifest_path}: sha256 不匹配或缺失 {raw}")
    return problems


def main() -> int:
    roots = [DEFAULT_DIR]
    custom = os.environ.get("CAPTCHA_ASSET_DIR", "").strip()
    if custom:
        roots.append(Path(custom))
    problems: list[str] = []
    for root in roots:
        if root.is_dir():
            problems.extend(check_root(root))
    if problems:
        print(f"[captcha-assets] 资产门禁失败 {len(problems)} 处：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print(f"[captcha-assets] 资产门禁通过：{len(roots)} 个 manifest。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
