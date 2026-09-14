#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""同步菜单权限点与模型字段（幂等修复命令）。

背景与设计：docs/architecture/菜单权限与字段同步补全方案-2026.09.md
- 端点漏登记权限点（含导入导出异步链、监控任务健康、我的填报等）→ 非超管 403；
- 权限点模型绑定缺失/错误 → 角色页无法配置字段白名单 / 导入导出无法回退到 list 菜单；
- ModelLabelField 依赖手工「生成数据」→ 新模型/字段长期缺失。

本命令按「批量生成权限」同一口径补齐权限点，可反复执行（幂等）：

    python manage.py sync_menu_permissions --dry-run        # 只报告，不写库
    python manage.py sync_menu_permissions                  # 字段树 + 权限点修复
    python manage.py sync_menu_permissions --update-seed    # 同时回写 loadjson 种子
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from system.models import Menu, MenuMeta, ModelLabelField, UserInfo
from system.utils import permission_sync as sync
from system.utils.modelfield import sync_model_field

DETAIL_LIMIT = 100


class Command(BaseCommand):
    help = "同步菜单权限点与模型字段：补齐缺失权限点、校正模型绑定、修复字段树（幂等）"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="只输出计划，不写库（字段同步也会跳过）")
        parser.add_argument("--skip-fields", action="store_true", help="跳过模型字段树同步")
        parser.add_argument("--skip-permissions", action="store_true", help="跳过菜单权限点同步")
        parser.add_argument(
            "--grant-roles",
            action="store_true",
            help="新建权限点自动授予「已拥有同模块权限点与父菜单」的角色（默认不授予）",
        )
        parser.add_argument("--update-seed", action="store_true", help="把变更合并回 loadjson 种子文件")
        parser.add_argument("--default-parent", default="", help="无法自动归属时的兜底父菜单 name（默认报告并跳过）")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        user = UserInfo.objects.filter(is_superuser=True).order_by("pk").first()

        if not options["skip_fields"]:
            self._sync_fields(dry_run)
        if not options["skip_permissions"]:
            self._sync_permissions(options, user, dry_run)
        if dry_run:
            self.stdout.write(self.style.WARNING("dry-run：未写入任何数据"))

    # ------------------------------------------------------------------ 字段树

    def _sync_fields(self, dry_run):
        if dry_run:
            self.stdout.write("[字段] dry-run：跳过字段树写入（去掉 --dry-run 执行实际同步）")
            return
        result = sync_model_field()
        role, data = result["role"], result["data"]
        self.stdout.write(
            f"[字段] 角色字段树 {role['kept']} 项（清理 {role['deleted']}）；"
            f"数据权限字段树 {data['kept']} 项（清理 {data['deleted']}，覆盖模型 {data['models']}）"
        )
        if role["failed_serializers"]:
            self.stdout.write(self.style.WARNING(f"[字段] 跳过异常序列化器：{role['failed_serializers']}"))

    # ------------------------------------------------------------------ 权限点

    def _sync_permissions(self, options, user, dry_run):
        default_parent = None
        if options["default_parent"]:
            default_parent = Menu.objects.filter(
                name=options["default_parent"], menu_type=Menu.MenuChoices.MENU, deleted_at__isnull=True
            ).first()

        routes = sync.build_route_index()
        perms = sync.load_permission_menus()
        gaps = sync.scan_gaps(routes, perms)
        plans, unresolved = sync.build_plans(gaps, routes, perms, default_parent)
        fixes = sync.plan_binding_fixes(routes, perms)
        unmatched, duplicates, exempted, known_duplicates = sync.audit_permission_menus(routes, perms)

        self.stdout.write(
            f"[权限] 扫描路由 {len(routes)} 条 / 权限点 {len(perms)} 个；"
            f"缺口 {len(gaps)} 条 → 计划新增 {len(plans)} 个（无法归属 {len(unresolved)}）"
        )
        for plan in plans[:DETAIL_LIMIT]:
            self.stdout.write(
                f"  + {plan.method:6s} {plan.url}  [{plan.code}]  ← 父菜单 {plan.parent_name or '-'}"
                f"（模型 {len(plan.model_pks)}）"
            )
        if len(plans) > DETAIL_LIMIT:
            self.stdout.write(f"  ... 其余 {len(plans) - DETAIL_LIMIT} 条略")
        for route, method, _action in unresolved[:DETAIL_LIMIT]:
            self.stdout.write(self.style.WARNING(f"  ? 无法归属：{method} {route.url}（view={route.view}）"))

        if fixes:
            self.stdout.write(f"[绑定] 计划校正模型绑定 {len(fixes)} 个权限点")
            for fix in fixes[:DETAIL_LIMIT]:
                detail = f"补 {len(fix.expected - fix.current)} 个模型" if fix.mode == "add" else "清空模型绑定"
                self.stdout.write(f"  * {fix.menu.name}（{fix.action}，{detail}）")

        if exempted or known_duplicates:
            self.stdout.write(
                f"[审计] 豁免 {len(exempted)} 个扫描面外权限点（chat/api-docs/flower 等无名路由）"
                f" + {len(known_duplicates)} 个已知同端点双权限码（不计入异常）"
            )
        for perm in unmatched[:DETAIL_LIMIT]:
            self.stdout.write(
                self.style.WARNING(f"  ~ 未匹配到路由的权限点（疑似历史/已删端点）：{perm.name} | {perm.path}")
            )
        for perm in duplicates[:DETAIL_LIMIT]:
            self.stdout.write(self.style.WARNING(f"  ~ 重复权限点：{perm.name} | {perm.path} | {perm.method}"))

        if dry_run:
            return

        created, conflicts, method_fixed = sync.apply_plans(plans, user)
        fixed_count = sync.apply_binding_fixes(fixes)
        self.stdout.write(
            f"[权限] 已新增 {len(created)} 个权限点，修正方法 {len(method_fixed)} 个，校正绑定 {fixed_count} 个"
        )
        for menu in method_fixed[:DETAIL_LIMIT]:
            self.stdout.write(f"  ^ 修正权限方法：{menu.name} → {menu.method}（{menu.path}）")
        for plan in conflicts[:DETAIL_LIMIT]:
            self.stdout.write(self.style.WARNING(f"  ! 权限码已存在，跳过：{plan.code}（{plan.method} {plan.url}）"))

        if options["grant_roles"] and created:
            parent_of_created = {menu.pk: menu.parent_id for menu in created}
            granted = sync.grant_to_roles(created, perms, parent_of_created)
            self.stdout.write(f"[授权] 已为 {len(granted)} 个（角色, 权限点）组合补授")

        if options["update_seed"]:
            # 回写范围 = 新增 + 方法修正 + 绑定校正（三类变更都必须落盘，否则下次
            # load_init_json 会用旧种子把它们覆盖回去）
            self._update_seed(created, [fix.menu for fix in fixes], method_fixed)

    # ------------------------------------------------------------------ 种子

    def _update_seed(self, created_menus, fixed_menus, method_fixed=None):
        root = Path(settings.PROJECT_DIR) / "loadjson"
        menus = {menu.pk: menu for menu in [*created_menus, *fixed_menus, *(method_fixed or [])]}
        # 库内新增但种子缺失的权限点一并补写（多次执行间也能收敛，如先执行修复再补种子）
        seed_pks = sync.seed_entry_pks(root / "menu.json")
        for menu in Menu.objects.filter(menu_type=Menu.MenuChoices.PERMISSION, deleted_at__isnull=True).exclude(
            pk__in=seed_pks
        ):
            menus.setdefault(menu.pk, menu)
        if menus:
            entries = sync.dump_entries(Menu, list(menus.values()))
            meta_entries = sync.dump_entries(MenuMeta, [menu.meta for menu in menus.values()])
            self.stdout.write(f"[种子] menu.json {sync.merge_seed_file(root / 'menu.json', entries)}")
            self.stdout.write(f"[种子] menumeta.json {sync.merge_seed_file(root / 'menumeta.json', meta_entries)}")

        field_entries = sync.dump_entries(
            ModelLabelField, ModelLabelField.objects.all(), exclude_fields=("updated_time",)
        )
        self.stdout.write(
            f"[种子] modellabelfield.json {sync.merge_seed_file(root / 'modellabelfield.json', field_entries)}"
        )
