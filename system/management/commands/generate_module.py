#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : generate_module
# author : ly_13
# date : 2026/09/17
"""生成第三方 app 的功能模块声明（可裁剪模块的脚手架）。

    python manage.py generate_module my_biz --app demo \
        --label "自有业务" --menu MyBizMenu --route "^/api/demo/"

生成 ``{app}/modules.py``（模块级 ``MODULES`` 声明），随 app 安装自动纳入
``python manage.py modules`` 清单与 config.yml 裁剪体系，无需改动本项目源码。
只写目标 app 自己的文件，不修改 core 的任何代码。

完整落地步骤见 docs/architecture/模块化与功能裁剪.md §九「路径 C」。
"""

import os

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError

from common.core.modules import CORE, MODULES, all_module_specs

HEADER = '''#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""{app_label} 的功能模块声明。

随 app 安装自动纳入模块清单（`python manage.py modules`）；等级决定各发行预设下
是否默认开启：core（不可裁）/ standard（默认开）/ optional（按需开）。
"""

from common.core.modules import ModuleSpec

MODULES = (
    ModuleSpec(
        id="{module_id}",
        label="{label}",
        level="{level}",
{extra}    ),
)
'''


class Command(BaseCommand):
    # argparse 的 help 必须是 str（惰性翻译对象会抛 TypeError，详见 modules.py 注释）
    help = "Scaffold a functional module declaration for an installed app"

    def add_arguments(self, parser):
        parser.add_argument("module_id", help="模块 id（config.yml 的 MODULE_ENABLE/DISABLE 使用）")
        parser.add_argument("--app", required=True, help="已安装的 app label（如 demo）")
        parser.add_argument("--label", default="", help="模块中文名（展示用，默认取模块 id）")
        parser.add_argument("--level", choices=(CORE, "standard", "optional"), default="optional", help="模块等级")
        parser.add_argument(
            "--menu", action="append", default=[], help="菜单根 name（可多次；需已在 loadjson/menu.json 登记）"
        )
        parser.add_argument("--route", action="append", default=[], help="请求路由前缀正则（可多次，如 ^/api/demo/）")
        parser.add_argument("--permission", action="append", default=[], help="补充权限点 path 前缀（可多次）")
        parser.add_argument("--force", action="store_true", help="覆盖已存在的 {app}/modules.py")

    def handle(self, *args, **options):
        module_id = options["module_id"]
        if module_id in {spec.id for spec in all_module_specs()}:
            raise CommandError(f"模块 id 已存在：{module_id}")

        app_label = options["app"]
        if app_label not in apps.app_configs:
            raise CommandError(
                f"未安装的 app：{app_label}；已安装：{', '.join(sorted(apps.app_configs))}"
                "（新 app 需先在 config.yml 的 XADMIN_APPS 注册）"
            )
        app_config = apps.get_app_config(app_label)

        # 声明了菜单根时提示：菜单 name 必须是种子/库中真实存在的，否则裁剪不生效
        known_menus = {name for spec in MODULES for name in spec.menus}
        unknown_menus = [name for name in options["menu"] if name not in known_menus]

        target = os.path.join(app_config.path, "modules.py")
        if os.path.exists(target) and not options["force"]:
            raise CommandError(f"目标文件已存在：{target}（如需覆盖加 --force）")

        extra_lines = []
        if options["menu"]:
            extra_lines.append(f"        menus={tuple(options['menu'])!r},")
        if options["route"]:
            extra_lines.append(f"        routes={tuple(options['route'])!r},")
        if options["permission"]:
            extra_lines.append(f"        permissions={tuple(options['permission'])!r},")
        content = HEADER.format(
            app_label=app_config.verbose_name or app_config.name,
            module_id=module_id,
            label=options["label"] or module_id,
            level=options["level"],
            extra="\n".join(extra_lines) + "\n" if extra_lines else "",
        )
        with open(target, "w", encoding="utf-8") as fp:
            fp.write(content)

        self.stdout.write(f"已生成：{os.path.relpath(target, os.path.dirname(app_config.path))}")
        if unknown_menus:
            self.stdout.write(
                self.style.WARNING(
                    f"注意：菜单 name {unknown_menus} 不在内置模块清单中——"
                    "需确认它已在 loadjson/menu.json 登记，否则裁剪不会命中"
                )
            )
        self.stdout.write("")
        self.stdout.write("后续步骤：")
        self.stdout.write("  1) 菜单：loadjson/menu.json 登记菜单根 + src/views 下放页面组件")
        self.stdout.write(f'  2) 周期任务：@register_as_period_task(..., module="{module_id}")')
        self.stdout.write("  3) 验证：python manage.py modules  # 清单中应出现该模块")
        self.stdout.write("  4) 裁剪：config.yml 配 MODULE_PRESET / MODULE_DISABLE，重启生效")
        self.stdout.write("  5) 参考：docs/architecture/模块化与功能裁剪.md §七/§九")
