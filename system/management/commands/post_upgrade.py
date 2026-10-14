#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""升级后数据补全（幂等）：一条命令替代「手动四件套」。

背景：升级后漏执行以下动作会造成静默故障——URL 前缀未平移（四域路由 404/403）、
种子/权限点未入库（非超管 403）、语言包未编译（中文界面回退英文）、配置缓存
陈旧（开关不生效）。

动作（按序，均可重复执行）：
1. ``upgrade_ledger --apply`` —— 认领 4.x 迁移账本（5.0 合并链；全新库与已认领库
   均为 no-op，不执行 DDL）；
2. ``migrate_api_prefixes --apply`` —— 存量库里的旧 API 前缀路径平移（四域路由
   独立前缀化；全新库为 0 命中 no-op）；
3. ``load_init_json``     —— 内置种子与新增权限点入库；
4. ``compilemessages``    —— 编译语言包（缺 gettext 时降级为警告）；
5. ``expire_caches config_*`` —— 失效配置缓存；
6. 权限点缺口扫描（只报告，不修改数据；修复命令 sync_menu_permissions）。

用法：
    python manage.py post_upgrade
    python manage.py post_upgrade --skip-seed --skip-compile
"""

from typing import Any

from django.core import management
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Post-upgrade tasks: API prefix migration + seed sync + compilemessages + cache invalidation"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--skip-seed", action="store_true", help="跳过 load_init_json（仅编译语言包与缓存）")
        parser.add_argument("--skip-compile", action="store_true", help="跳过 compilemessages（无 gettext 环境）")
        parser.add_argument("--skip-permissions", action="store_true", help="跳过权限点缺口扫描")

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write("[1/6] 迁移账本认领（5.0 合并链；全新库与已认领库为 no-op）…")
        try:
            management.call_command("upgrade_ledger", apply=True)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 未知链路只告警，migrate 会给出硬失败
            self.stdout.write(self.style.WARNING(f"      账本认领失败（若库来自更早版本请先 upgrade_check）：{exc}"))

        self.stdout.write("[2/6] API 前缀平移（存量库 /api/system/<四域> → 独立前缀，幂等）…")
        try:
            management.call_command("migrate_api_prefixes", "--apply")
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"      前缀平移失败：{exc}"))

        if options["skip_seed"]:
            self.stdout.write("[3/6] 跳过种子导入（--skip-seed）")
        else:
            self.stdout.write("[3/6] 导入内置种子（load_init_json，幂等）…")
            management.call_command("load_init_json")

        if options["skip_compile"]:
            self.stdout.write("[4/6] 跳过语言包编译（--skip-compile）")
        else:
            self.stdout.write("[4/6] 编译语言包（compilemessages）…")
            try:
                management.call_command("compilemessages", verbosity=0)
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"      编译失败（界面文案可能回退英文）：{exc}"))

        self.stdout.write("[5/6] 失效配置缓存…")
        try:
            management.call_command("expire_caches", "config_*")
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"      缓存失效失败：{exc}"))

        if options["skip_permissions"]:
            self.stdout.write("[6/6] 跳过权限点扫描（--skip-permissions）")
        else:
            self._scan_permissions()

        self.stdout.write(self.style.SUCCESS("post_upgrade 完成"))

    def _scan_permissions(self) -> None:
        self.stdout.write("[5/5] 权限点缺口扫描…")
        try:
            from identity.models import UserInfo

            if not UserInfo.objects.exists():
                self.stdout.write("      数据库尚未初始化，跳过（先执行 python ops/init_data.py）")
                return
            from system.services import permission_sync as sync

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
