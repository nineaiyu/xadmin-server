#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : modules
# author : ly_13
# date : 2026/09/17
"""功能模块清单与裁剪预演（二开友好）。

用法：

    python manage.py modules                          # 当前生效的模块组合
    python manage.py modules --preset standard        # 预演另一套预设（不改配置）
    python manage.py modules --disable analysis,chat  # 预演关闭若干模块
    python manage.py modules --preset core --enable chat --config   # 只输出 config.yml 片段
    python manage.py modules --clear-override         # 恢复通道：删除后台覆盖行

模块清单、裁剪语义与维护约定见 docs/architecture/模块化与功能裁剪.md。
"""

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from common.core.modules import (
    CORE,
    PRESETS,
    all_module_specs,
    clear_override,
    config_snippet,
    modules_report,
    preview_modules,
    resolve_modules,
)

LEVEL_LABELS = {CORE: "内核", "standard": "标配", "optional": "可选"}


class Command(BaseCommand):
    # 注意：argparse 的 help 必须是 str —— 惰性翻译对象（gettext_lazy）在
    # Python 3.12+ 的 argparse 文本换行处理中会抛 TypeError（见 ADR-045 附注）
    help = "List functional modules and preview a trimmed combination"

    def add_arguments(self, parser):
        parser.add_argument("--preset", choices=PRESETS, help="按指定预设预演（不改变实际配置）")
        parser.add_argument("--enable", default=None, help="额外启用的模块 id，逗号分隔")
        parser.add_argument("--disable", default=None, help="额外停用的模块 id，逗号分隔")
        parser.add_argument("--config", action="store_true", help="只输出可粘贴到 config.yml 的配置片段")
        parser.add_argument(
            "--impact",
            action="store_true",
            help="展示该组合在当前库上的影响面（只读：隐藏的页面/权限点、受影响角色）",
        )
        parser.add_argument(
            "--clear-override",
            action="store_true",
            help="删除管理页写入的后台覆盖行（覆盖引用已移除模块导致启动失败时的恢复通道）",
        )

    def handle(self, *args, **options):
        # 恢复通道：必须在任何模块解析之前执行——覆盖行非法时解析本身就会 fail-fast
        if options.get("clear_override"):
            removed = clear_override()
            self.stdout.write(
                f"已清除后台覆盖行 {removed} 行；模块组合恢复为部署基线（config.yml / 环境变量），重启进程后生效。"
            )
            return

        preview = any(
            [
                options.get("preset"),
                options.get("enable") is not None,
                options.get("disable") is not None,
            ]
        )
        try:
            resolution = (
                preview_modules(
                    preset=options.get("preset"),
                    enable=options.get("enable"),
                    disable=options.get("disable"),
                )
                if preview
                else resolve_modules()
            )
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from None

        if options.get("config"):
            self._write_config(resolution)
            return

        self._write_table(resolution, preview=preview)
        if options.get("impact"):
            self._write_impact(resolution)
        self._write_config(resolution)

    def _write_table(self, resolution, preview: bool):
        title = "功能模块清单（预演，未改动实际配置）" if preview else "功能模块清单（当前生效）"
        self.stdout.write(f"{title}：preset={resolution.preset}")
        self.stdout.write("")
        rows = modules_report(resolution)
        level_order = {CORE: 0, "standard": 1, "optional": 2}
        for item in sorted(rows, key=lambda row: (level_order.get(row["level"], 9), row["id"])):
            state = "启用" if item["enabled"] else "停用"
            self.stdout.write(
                f"  {item['id']:<24}{LEVEL_LABELS.get(item['level'], item['level']):<6}"
                f"{state}  "
                f"页面 {item['menus']} / 路由 {item['routes']}  {item['label']}"
            )
        self.stdout.write("")
        disabled = sorted(resolution.disabled)
        self.stdout.write(f"已启用 {len(resolution.enabled)} / {len(all_module_specs())} 个模块")
        if disabled:
            self.stdout.write(f"停用：{', '.join(disabled)}")
            self.stdout.write("停用影响：请求 404 + 菜单与权限点隐藏 + 周期任务不注册（业务数据保留）")
        self.stdout.write("")

    def _write_impact(self, resolution):
        from django.db import OperationalError, ProgrammingError

        from system.utils.module_impact import module_impact

        if not resolution.disabled:
            self.stdout.write("影响面：全量启用，无菜单/权限点被隐藏。")
            self.stdout.write("")
            return

        try:
            impact = module_impact(resolution)
        except ProgrammingError as exc:
            raise CommandError(f"菜单表不可用（是否已执行 migrate？）：{exc}") from None
        except OperationalError as exc:
            raise CommandError(f"无法读取数据库（检查 DB 配置与连通性）：{exc}") from None

        total, hidden = impact["total"], impact["hidden"]
        self.stdout.write(
            f"影响面（当前库实时数据，只读）：菜单行 "
            f"{total['directories'] + total['pages'] + total['permissions']}"
            f"（目录 {total['directories']} / 页面 {total['pages']} / 权限点 {total['permissions']}）"
        )
        self.stdout.write(
            f"  将隐藏：目录 {hidden['directories']} / 页面 {hidden['pages']} / 权限点 {hidden['permissions']}"
            f"    仍可见：页面 {total['pages'] - hidden['pages']} / 权限点 {total['permissions'] - hidden['permissions']}"
        )
        self.stdout.write("")
        self.stdout.write("  模块                      等级      目录  页面  权限点  路由")
        for item in impact["modules"]:
            self.stdout.write(
                f"  {item['id']:<24}{LEVEL_LABELS.get(item['level'], item['level']):<8}"
                f"{item['directories']:>5}{item['pages']:>6}{item['permissions']:>8}{item['routes']:>6}"
            )
        self.stdout.write("")
        self.stdout.write("  受影响角色（绑定菜单 / 其中被隐藏 / 在册用户；授权数据无需改动，运行期过滤）")
        for role in impact["roles"]:
            if not role["hidden"]:
                continue
            self.stdout.write(f"    {role['name']:<24}{role['bound']:>5} /{role['hidden']:>5} /{role['users']:>5}")
        if impact["tables"]:
            table_total = sum(item["rows"] for item in impact["tables"])
            self.stdout.write("")
            self.stdout.write(
                f"  相关业务数据保留：命中可选模块的 {len(impact['tables'])} 张表共约 {table_total} 行（裁剪不删除任何行）"
            )
        self.stdout.write("")

    def _write_config(self, resolution):
        self.stdout.write("# 粘贴到 xadmin-server/config.yml 后重启进程生效")
        self.stdout.write(config_snippet(resolution))
        self.stdout.write("# 模块清单与裁剪语义：docs/architecture/模块化与功能裁剪.md")
