#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""clean cut 守护：旧图片码模式 / 旧字段 / 旧模型不得复活。

项目未上线阶段已删除 ``CAPTCHA_MODE``、旧图片码端点、``CaptchaStore`` /
``CaptchaAuth`` 与 ``captcha_key`` / ``captcha_code``。本门禁扫描运行时代码，
防止后续改动把旧链路重新接回来。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENT_ROOT = ROOT.parent / "xadmin-client"

# 只扫运行时代码：tests / migrations / docs / 脚本自身不参与（负向断言与历史迁移合法）
SCAN_TARGETS = (
    ROOT / "captcha",
    ROOT / "identity",
    ROOT / "settings",
    ROOT / "server",
    ROOT / "system",
    CLIENT_ROOT / "src",
)

FORBIDDEN = (
    "CAPTCHA_MODE",
    "CAPTCHA_INTERACTIVE_TYPES",
    "captcha_mode",
    "captcha_key",
    "captcha_code",
    "CaptchaStore",
    "CaptchaAuth",
    "captcha.views",
    "captcha.urls",
    "captcha.utils",
)

# 仅文档字符串说明历史 / 生成契约 / 负向测试允许出现
ALLOWLIST = {
    "captcha/backends/image.py",
    "captcha/rendering.py",
    "captcha/migrations/0001_initial.py",
    "captcha/migrations/0004_delete_captchastore.py",
    "settings/migrations/0002_captcha_unified_settings.py",
    "tests",
}


def _relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return f"xadmin-client/{path.relative_to(CLIENT_ROOT).as_posix()}"


def _allowed(relative: str) -> bool:
    return any(relative == item or relative.startswith(f"{item}/") for item in ALLOWLIST)


def _iter_files() -> list[Path]:
    files: list[Path] = []
    for target in SCAN_TARGETS:
        if not target.is_dir():
            continue
        files.extend(
            path
            for path in sorted(target.rglob("*"))
            if path.is_file() and path.suffix in {".py", ".ts", ".vue", ".json"}
        )
    return files


def main() -> int:
    violations: list[str] = []
    for path in _iter_files():
        relative = _relative(path)
        if _allowed(relative):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for token in FORBIDDEN:
            if token in text:
                violations.append(f"{relative}: 出现已删除符号 {token}")
    if violations:
        print(f"[captcha-legacy] clean cut 违规 {len(violations)} 处：")
        for item in violations:
            print(f"  - {item}")
        print("修复：旧图片码链路已删除；新增能力请走统一 challenge/verify/ticket 协议。")
        return 1
    print("[captcha-legacy] clean cut 通过：未发现旧图片码模式/字段/模型。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
