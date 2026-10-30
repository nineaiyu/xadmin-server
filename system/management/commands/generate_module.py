#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : generate_module
# author : ly_13
# date : 2026/09/17
"""生成第三方 app 的功能模块声明（可裁剪模块的脚手架）。

    python manage.py generate_module my_biz --app demo \
        --label "自有业务" --menu MyBizMenu --route "^/api/demo/" \
        --with-seeds --sync

生成 ``{app}/modules.py``（模块级 ``MODULES`` 声明），随 app 安装自动纳入
``python manage.py modules`` 清单与 config.yml 裁剪体系，无需改动本项目源码。
只写目标 app 自己的文件，不修改 core 的任何代码。

``--with-seeds`` 一并生成种子四件套骨架（manifest/menu/menumeta/空字段容器）
与 ``ai_declarations.py`` 骨架；``--sync`` 串联既有命令闭环：
``load_init_json``（灌容器）→ ``sync_menu_permissions --update-seed
--default-parent <菜单>``（按路由扫描回写权限点 + 字段树），
把「新增业务模块」从 10 步手改压缩到 3 步（声明脚手架 → 写业务代码 → 联动同步）。

完整落地步骤见 docs/architecture/模块化与功能裁剪.md §九「路径 C」。
"""

import os
from typing import Any

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from common.core.modules import CORE, MODULES, module_id_conflict, render_modules_source
from system.services.module_scaffold import render_seed_skeleton


class Command(BaseCommand):
    # argparse 的 help 必须是 str（惰性翻译对象会抛 TypeError，详见 modules.py 注释）
    help = "Scaffold a functional module declaration for an installed app"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("module_id", help="模块 id（config.yml 的 MODULE_ENABLE/DISABLE 使用）")
        parser.add_argument("--app", required=True, help="已安装的 app label（如 demo）")
        parser.add_argument("--label", default="", help="模块中文名（展示用，默认取模块 id）")
        parser.add_argument("--level", choices=(CORE, "standard", "optional"), default="optional", help="模块等级")
        parser.add_argument(
            "--menu",
            action="append",
            default=[],
            help="菜单根 name（可多次；--with-seeds 时按它生成页面根骨架）",
        )
        parser.add_argument("--route", action="append", default=[], help="请求路由前缀正则（可多次，如 ^/api/demo/）")
        parser.add_argument("--permission", action="append", default=[], help="补充权限点 path 前缀（可多次）")
        parser.add_argument("--force", action="store_true", help="覆盖已存在的 {app}/modules.py")
        parser.add_argument("--with-seeds", action="store_true", help="一并生成种子四件套 + ai_declarations 骨架")
        parser.add_argument(
            "--sync",
            action="store_true",
            help="--with-seeds 后串联 load_init_json + sync_menu_permissions --update-seed（回写权限点）",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        module_id = options["module_id"]
        if module_id_conflict(module_id):
            raise CommandError(f"模块 id 已存在：{module_id}")

        app_label = options["app"]
        if app_label not in apps.app_configs:
            raise CommandError(
                f"未安装的 app：{app_label}；已安装：{', '.join(sorted(apps.app_configs))}"
                "（新 app 需先在 config.yml 的 XADMIN_APPS 注册）"
            )
        app_config = apps.get_app_config(app_label)
        if (options["with_seeds"] or options["sync"]) and not options["menu"]:
            raise CommandError("--with-seeds/--sync 需要至少一个 --menu（页面根骨架与权限点父菜单都由它承载）")

        # 声明了菜单根时提示：菜单 name 必须是种子/库中真实存在的，否则裁剪不生效
        known_menus = {name for spec in MODULES for name in spec.menus}
        unknown_menus = [name for name in options["menu"] if name not in known_menus]

        target = os.path.join(app_config.path, "modules.py")
        if os.path.exists(target) and not options["force"]:
            raise CommandError(f"目标文件已存在：{target}（如需覆盖加 --force）")

        content = render_modules_source(
            app_title=app_config.verbose_name or app_config.name,
            module_id=module_id,
            label=options["label"] or module_id,
            level=options["level"],
            menus=tuple(options["menu"]),
            routes=tuple(options["route"]),
            permissions=tuple(options["permission"]),
        )
        with open(target, "w", encoding="utf-8") as fp:
            fp.write(content)

        self.stdout.write(f"已生成：{os.path.relpath(target, os.path.dirname(app_config.path))}")
        if unknown_menus:
            self.stdout.write(
                self.style.WARNING(
                    f"注意：菜单 name {unknown_menus} 不在内置模块清单中——"
                    "需确认它已在应用层 seeds/menu.json 登记，否则裁剪不会命中"
                )
            )

        if options["with_seeds"] or options["sync"]:
            self._write_seed_skeleton(
                app_config.path,
                app_label,
                module_id,
                options["label"] or module_id,
                tuple(options["menu"]),
                tuple(options["route"]),
            )
        if options["sync"]:
            self._run_seed_sync(module_id, options["menu"])
        self.stdout.write("")
        self.stdout.write("后续步骤：")
        self.stdout.write("  1) 业务代码：models / serializers / views（可用 devtools 代码生成）")
        if not options["with_seeds"]:
            self.stdout.write("  2) 种子：<app>/seeds/ 登记 menu/manifest（或改用 --with-seeds 自动生成骨架）")
        self.stdout.write(f"  3) 验证：python manage.py modules  # 清单中应出现 {module_id}")
        self.stdout.write("  4) 裁剪：config.yml 配 MODULE_PRESET / MODULE_DISABLE，重启生效")
        self.stdout.write("  5) 参考：docs/architecture/模块化与功能裁剪.md §七/§九")

    def _write_seed_skeleton(
        self, app_path: str, app: str, module_id: str, label: str, menus: tuple[str, ...], routes: tuple[str, ...]
    ) -> None:
        """写入种子四件套 + ai_declarations 骨架；已存在的文件一律跳过（防覆盖手写内容）。"""
        files = render_seed_skeleton(app, module_id, label, menus, routes)
        for rel_path, content in files.items():
            path = os.path.join(app_path, rel_path)
            if os.path.exists(path):
                self.stdout.write(self.style.WARNING(f"跳过（已存在）：{app}/{rel_path}"))
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(content)
            self.stdout.write(f"已生成：{app}/{rel_path}")
        self.stdout.write(
            "下一步（权限点/字段树由既有命令回写，不手写）：\n"
            "  python manage.py load_init_json\n"
            f"  python manage.py sync_menu_permissions --update-seed --default-parent {menus[0]}"
        )

    def _run_seed_sync(self, module_id: str, menus: list[str]) -> None:
        """串联回写闭环：灌容器 → 扫描路由回写权限点（新 app 路由无前缀映射，显式给默认父菜单）。"""
        self.stdout.write(f"[sync] load_init_json → sync_menu_permissions --update-seed（模块 {module_id}）")
        call_command("load_init_json", stdout=self.stdout)
        call_command("sync_menu_permissions", "--update-seed", "--default-parent", menus[0], stdout=self.stdout)
