#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""升级后数据补全（幂等）：一条命令替代「手动三件套」。

背景：升级后漏执行以下动作会造成静默故障——种子/权限点未入库（非超管 403）、
语言包未编译（中文界面回退英文）、配置缓存陈旧（开关不生效）。

动作（按序，均可重复执行）：
1. ``load_init_json``     —— 内置种子与新增权限点入库；
2. ``compilemessages``    —— 编译语言包（缺 gettext 时降级为警告）；
3. ``expire_caches config_*`` —— 失效配置缓存；
4. 权限点缺口扫描（只报告，不修改数据；修复命令 sync_menu_permissions）。

用法：
    python manage.py post_upgrade
    python manage.py post_upgrade --skip-seed --skip-compile
"""

from django.core import management
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Post-upgrade tasks: seed sync + compilemessages + cache invalidation (+ permission scan)"

    def add_arguments(self, parser):
        parser.add_argument("--skip-seed", action="store_true", help="跳过 load_init_json（仅编译语言包与缓存）")
        parser.add_argument("--skip-compile", action="store_true", help="跳过 compilemessages（无 gettext 环境）")
        parser.add_argument("--skip-permissions", action="store_true", help="跳过权限点缺口扫描")

    def handle(self, *args, **options):
        if options["skip_seed"]:
            self.stdout.write("[1/4] 跳过种子导入（--skip-seed）")
        else:
            self.stdout.write("[1/4] 导入内置种子（load_init_json，幂等）…")
            management.call_command("load_init_json")

        if options["skip_compile"]:
            self.stdout.write("[2/4] 跳过语言包编译（--skip-compile）")
        else:
            self.stdout.write("[2/4] 编译语言包（compilemessages）…")
            try:
                management.call_command("compilemessages", verbosity=0)
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"      编译失败（界面文案可能回退英文）：{exc}"))

        self.stdout.write("[3/4] 失效配置缓存…")
        try:
            management.call_command("expire_caches", "config_*")
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"      缓存失效失败：{exc}"))

        if options["skip_permissions"]:
            self.stdout.write("[4/4] 跳过权限点扫描（--skip-permissions）")
        else:
            self._scan_permissions()

        self.stdout.write(self.style.SUCCESS("post_upgrade 完成"))

    def _scan_permissions(self):
        self.stdout.write("[4/4] 权限点缺口扫描…")
        try:
            from system.models import UserInfo

            if not UserInfo.objects.exists():
                self.stdout.write("      数据库尚未初始化，跳过（先执行 python utils/init_data.py）")
                return
            from system.utils import permission_sync as sync

            gaps = sync.scan_permission_gaps()
            if gaps:
                self.stdout.write(
                    self.style.WARNING(
                        f"      检测到 {len(gaps)} 条缺口（非超管将 403）：python manage.py sync_menu_permissions"
                    )
                )
            else:
                self.stdout.write(self.style.SUCCESS("      权限点完整覆盖"))
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"      扫描失败（跳过）：{exc}"))
