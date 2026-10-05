#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：`--register-app` 应用注册（config.yml 的 XADMIN_APPS 自动写入）。

定位：消除「生成四件套后还要手改 config.yml」的注册断点。写入是**文本级幂等编辑**
（保留原文件的注释与排版），三种形态分别处理：

- ``XADMIN_APPS: [demo]`` 内联列表 → 扩展列表；
- ``XADMIN_APPS:``（值在后续 ``- item`` 行或为空）→ 键行后插入一项；
- 无该键（或仅注释示例）→ 文件末尾追加键块。

不会动的文件：``config.py`` 形态（Python 源码不做文本编辑，给等价指引）、
无任何用户配置（回落 config_example.yml，指引 ``cp`` 后再注册）。
写入后用 yaml.safe_load 复验，失败即放弃写入（宁可退回手工指引，不产生坏配置）。
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from django.conf import settings

if TYPE_CHECKING:  # 宿主 Command（BaseCommand + 兄弟 mixin）提供的接口（mixin 模式）
    stdout: Any

CONFIG_NAMES = ("config.yml", "config.yaml")
XADMIN_APPS_KEY = "XADMIN_APPS"
# 只匹配行首未注释的键（MULTILINE ^）；`# XADMIN_APPS: [demo]` 这类注释示例不算数
_KEY_RE = re.compile(rf"^{XADMIN_APPS_KEY}:(?P<inline>.*)$", re.MULTILINE)


class RegistrationMixin:
    """`--register-app`：应用注册到 config.yml（幂等，打印 diff）。"""

    if TYPE_CHECKING:  # 宿主 Command（BaseCommand + 兄弟 mixin）提供的接口（mixin 模式）
        stdout: Any
        style: Any

    def _register_app(self, ctx, options) -> bool:
        """把 ``ctx["app_label"]`` 写入配置文件；返回是否已确保注册（含本就注册）。

        ``--dry-run`` 只预演不落盘（返回 False，后续步骤保留手工提示）。
        """
        app_label = ctx["app_label"]
        if app_label in (getattr(settings, XADMIN_APPS_KEY, None) or []):
            self.stdout.write(f"--register-app: {app_label} 已在 XADMIN_APPS 中，跳过")
            return True
        if options.get("dry_run"):
            root = self._config_root(options)
            self.stdout.write(f"--register-app (dry-run): 将把 {app_label} 写入 {root / 'config.yml'}")
            return False

        root = self._config_root(options)
        config_path = self._find_config(root)
        if config_path is None:
            self.stdout.write(
                self.style.WARNING(
                    "--register-app: 未找到 config.yml（当前为 config_example.yml 回落），请先：\n"
                    "  cp config_example.yml config.yml\n"
                    f"再把 XADMIN_APPS 改为包含 {app_label!r} 后重跑本命令"
                )
            )
            return False

        original = config_path.read_text(encoding="utf-8")
        try:
            parsed = yaml.safe_load(original) or {}
        except yaml.YAMLError:
            parsed = None
        if not isinstance(parsed, dict):
            self.stdout.write(
                self.style.WARNING(
                    f"--register-app: {config_path.name} 无法按 YAML 解析，请手工把 {app_label!r} 加入 XADMIN_APPS"
                )
            )
            return False

        current = parsed.get(XADMIN_APPS_KEY) or []
        if isinstance(current, str):
            current = [current]
        if not isinstance(current, list):
            self.stdout.write(
                self.style.WARNING(
                    f"--register-app: XADMIN_APPS 非 list 形态，请手工把 {app_label!r} 加入 {config_path.name}"
                )
            )
            return False
        if app_label in current:
            self.stdout.write(f"--register-app: {app_label} 已在 {config_path.name} 的 XADMIN_APPS 中，跳过")
            return True

        updated = self._upsert_key(original, app_label, inline_allowed=isinstance(parsed.get(XADMIN_APPS_KEY), list))
        if updated is None:
            self.stdout.write(
                self.style.WARNING(
                    f"--register-app: 未能安全编辑 {config_path.name}，请手工把 {app_label!r} 加入 XADMIN_APPS"
                )
            )
            return False
        try:
            verified = (yaml.safe_load(updated) or {}).get(XADMIN_APPS_KEY)
        except yaml.YAMLError:
            verified = None
        if not isinstance(verified, list) or app_label not in verified:
            self.stdout.write(
                self.style.WARNING(
                    f"--register-app: 写入后复验失败（已放弃修改），请手工把 {app_label!r} 加入 XADMIN_APPS"
                )
            )
            return False

        config_path.write_text(updated, encoding="utf-8")
        for line in self._diff_lines(original, updated):
            self.stdout.write(line)
        self.stdout.write(
            f"--register-app: 已写入 {config_path.name}（重启进程后路由/迁移才纳入 {app_label}；"
            "docker 部署: docker compose restart server celery-worker celery-heavy celery-beat）"
        )
        return True

    # ------------------------------------------------------------------ 细节

    @staticmethod
    def _config_root(options) -> Path:
        """配置文件查找根：与产物输出根一致（--output 可整体隔离，测试注入用）。"""

        return Path(options["output"] or settings.PROJECT_DIR)

    @classmethod
    def _find_config(cls, root: Path) -> Path | None:
        """用户配置文件（config.yml 优先于 config.yaml）；config.py 形态不在此处理。"""

        for name in CONFIG_NAMES:
            path = root / name
            if path.is_file():
                return path
        return None

    @classmethod
    def _upsert_key(cls, text: str, app_label: str, inline_allowed: bool) -> str | None:
        """在配置文本中注册 app；返回 None 表示无法安全编辑。"""

        match = _KEY_RE.search(text)
        if match is None:
            return cls._append_key(text, app_label)
        inline = match.group("inline").strip()
        if not inline:
            # 键存在但行内无值：块列表（值在后续 - item 行）或纯空键——键行后插入一项
            return cls._insert_after_key(text, match, app_label)
        if not inline_allowed or not (inline.startswith("[") and inline.endswith("]")):
            return None
        items = [item.strip().strip("\"'") for item in inline[1:-1].split(",") if item.strip()]
        items.append(app_label)
        start, end = match.span("inline")
        return text[:start] + f" [{', '.join(items)}]" + text[end:]

    @staticmethod
    def _insert_after_key(text: str, match: re.Match, app_label: str) -> str:
        """键行后插入 ``- app``（默认两空格缩进，与 config_example.yml 的块列表一致）。"""

        lines = text.splitlines(keepends=True)
        key_index = text.count("\n", 0, match.start())
        lines.insert(key_index + 1, f"  - {app_label}\n")
        return "".join(lines)

    @staticmethod
    def _append_key(text: str, app_label: str) -> str:
        """键不存在（或仅注释示例）：文件末尾追加键块。"""

        block = (
            "\n# 二开应用注册（generate_crud --register-app 追加）：参与 makemigrations / migrate / 路由自动注入\n"
            f"{XADMIN_APPS_KEY}:\n  - {app_label}\n"
        )
        return text.rstrip("\n") + "\n" + block

    @staticmethod
    def _diff_lines(original: str, updated: str) -> list[str]:
        """打印变更行（+ 前缀标新增行；XADMIN_APPS 行始终展示，便于核对）。"""

        old_lines = set(original.splitlines())
        diff = []
        for line in updated.splitlines():
            added = line not in old_lines
            if added or line.startswith(XADMIN_APPS_KEY):
                diff.append(f"  {'+' if added else ' '} {line}")
        return diff
