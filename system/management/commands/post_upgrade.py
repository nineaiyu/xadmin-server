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
        parser.add_argument(
            "--skip-field-permissions",
            action="store_true",
            help="跳过字段白名单补齐（默认自动补齐：只补「无行/空行/脱节」，不改管理员已配置的裁剪）",
        )

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
            self._scan_permissions(skip_field_permissions=options["skip_field_permissions"])

        self.stdout.write(self.style.SUCCESS("post_upgrade 完成"))

    def _scan_permissions(self, skip_field_permissions: bool = False) -> None:
        self.stdout.write("[6/6] 权限点与字段白名单…")
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
                        f"      检测到 {len(gaps)} 条权限点缺口（非超管将 403）：python manage.py sync_menu_permissions"
                    )
                )
            else:
                self.stdout.write(self.style.SUCCESS("      权限点完整覆盖"))
            self._fix_field_permissions(sync, skip=skip_field_permissions)
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"      扫描失败（跳过）：{exc}"))

    def _fix_field_permissions(self, sync: Any, skip: bool = False) -> None:
        """补齐字段白名单并清缓存。

        字段权限是 fail-closed 的零字段口径：某「角色 × 权限点」的白名单为空或与
        绑定模型脱节时，列表/详情输出空对象、``search-columns`` 返回空数组，表现为
        「有菜单、没有列」。4.x 没有这道门，升级后的存量角色极易命中（种子只静态列举
        部分模型），因此在收尾阶段**自动补齐**：先补菜单→模型绑定，再按接口自身模型
        逐行补白名单（只加不减，见 ``_complete_field_whitelists``）。
        """
        if skip:
            self.stdout.write("[6/6] 跳过字段白名单补齐（--skip-field-permissions）")
            return
        bound = self._bind_legacy_menu_models()
        if bound:
            self.stdout.write(
                self.style.SUCCESS(f"      权限菜单补模型绑定：{bound} 个（升级库的老菜单缺 model 绑定）")
            )
        completed = self._complete_field_whitelists()
        if completed:
            self.stdout.write(self.style.SUCCESS(f"      字段白名单补齐：{completed} 个（角色, 权限点）"))
        if bound or completed:
            self._clear_metadata_cache()
        gaps = sync.audit_field_permissions()
        if gaps:
            self.stdout.write(
                self.style.WARNING(
                    f"      仍有 {len(gaps)} 个（角色, 权限点）字段白名单缺口："
                    "python manage.py sync_menu_permissions --fix-field-permissions --update-seed"
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("      字段白名单完整覆盖"))

    def _bind_legacy_menu_models(self) -> int:
        """把「接口自己的模型」补进权限菜单的 model 绑定（并集，不删已有绑定）。

        4.x 的权限菜单没有「模型」概念；5.x 的字段白名单、角色页字段权限都以
        ``menu.model`` 为准（``permission_sync.model_field_nodes`` 读它）。升级库上这些
        菜单要么没有绑定，要么只绑了**关联模型**（`identity.userinfo` 等）而缺**接口自己的
        模型**——于是白名单补出来的是错模型的字段，页面自己的列仍然是空（fail-closed 零字段）。

        这里按「菜单 API 路径 → 视图 → 序列化器 → 模型」把接口自己的模型**并入**绑定。
        """
        from django.urls import Resolver404, resolve

        from system.models import Menu, ModelLabelField

        bound = 0
        for menu in Menu.objects.filter(menu_type=Menu.MenuChoices.PERMISSION).exclude(path=""):
            path = (menu.path or "").strip()
            if not path.startswith("api/") or "(" in path or "?" in path:
                continue
            url = "/" + path.rstrip("$").replace("$", "")
            try:
                matched = resolve(url)
            except Resolver404:
                continue
            view_class = getattr(matched.func, "cls", None) or getattr(matched.func, "view_class", None)
            serializer_class = getattr(view_class, "serializer_class", None)
            model = getattr(getattr(serializer_class, "Meta", None), "model", None)
            if model is None:
                continue
            roots = list(ModelLabelField.objects.filter(parent__isnull=True, name=model._meta.label_lower))
            if not roots:
                continue
            existing = set(menu.model.values_list("pk", flat=True))
            missing = [root for root in roots if root.pk not in existing]
            if missing:
                menu.model.add(*missing)
                bound += 1
        return bound

    def _complete_field_whitelists(self) -> int:
        """按权限点绑定模型补齐白名单（**只加不减**，含新增字段）。

        与 ``sync_field_permissions`` 共用 ``complete_field_permissions``：按
        「权限点绑定模型下全部 ROLE 字段节点」补缺，不删除管理员已收敛的字段。
        """
        from system.services.permission_sync import complete_field_permissions

        return len(complete_field_permissions())

    def _clear_metadata_cache(self) -> None:
        """清空缓存：列/字段元数据按「用户 + 模型」缓存，白名单变更后必须失效。"""
        try:
            from django.core.cache import cache

            cache.clear()
            self.stdout.write("      已清空缓存（列/字段元数据）")
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"      清缓存失败（可能短暂显示旧列）：{exc}"))
