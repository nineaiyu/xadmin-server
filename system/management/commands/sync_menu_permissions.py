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
    python manage.py sync_menu_permissions --update-seed    # 同时按应用归属回写各层种子
"""

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from identity.models import UserInfo
from system.models import FieldPermission, Menu, MenuMeta, ModelLabelField
from system.services import permission_sync as sync
from system.services.modelfield import sync_model_field
from system.services.seeding import PLATFORM, discover, read_model_rows, row_owners, write_layer_model

DETAIL_LIMIT = 100


class Command(BaseCommand):
    help = "同步菜单权限点与模型字段：补齐缺失权限点、校正模型绑定、修复字段树（幂等）"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dry-run", action="store_true", help="只输出计划，不写库（字段同步也会跳过）")
        parser.add_argument("--skip-fields", action="store_true", help="跳过模型字段树同步")
        parser.add_argument("--skip-permissions", action="store_true", help="跳过菜单权限点同步")
        parser.add_argument(
            "--grant-roles",
            action="store_true",
            help="新建权限点自动授予「已拥有同模块权限点与父菜单」的角色（默认不授予）",
        )
        parser.add_argument(
            "--update-seed",
            action="store_true",
            help="把变更按应用归属回写各层种子（平台层 loadjson/ + <app>/seeds/）",
        )
        parser.add_argument(
            "--fix-field-permissions",
            action="store_true",
            help="补齐字段白名单（含新增字段；只增不减，不改写已有裁剪）",
        )
        parser.add_argument("--default-parent", default="", help="无法自动归属时的兜底父菜单 name（默认报告并跳过）")

    def handle(self, *args: Any, **options: Any) -> None:
        dry_run = options["dry_run"]
        user = UserInfo.objects.filter(is_superuser=True).order_by("pk").first()

        if not options["skip_fields"]:
            self._sync_fields(dry_run)
        if not options["skip_permissions"]:
            self._sync_permissions(options, user, dry_run)
        elif options["fix_field_permissions"]:
            # 与权限点扫描解耦的独立入口：迁移/升级后只想补字段白名单时免全量扫描。
            # 该入口不做绑定校正，判定与取值基于库内现有绑定（适用已校正过绑定的库）
            self._report_field_permission_gaps()
            if dry_run:
                self._report_pending_field_nodes()
            else:
                self._fill_field_permissions()
                if options["update_seed"]:
                    self._update_field_permission_seed(discover().manifests)
        if dry_run:
            self.stdout.write(self.style.WARNING("dry-run：未写入任何数据"))

    # ------------------------------------------------------------------ 字段树

    def _sync_fields(self, dry_run: Any) -> None:
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

    def _sync_permissions(self, options: Any, user: Any, dry_run: Any) -> None:
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
                if fix.mode == "clear":
                    detail = "清空模型绑定"
                elif fix.mode == "shrink":
                    detail = f"收缩 {len(fix.current - fix.expected)} 个陈旧模型"
                elif fix.mode == "adjust":
                    detail = f"补 {len(fix.expected - fix.current)} 个、收缩 {len(fix.current - fix.expected)} 个模型"
                else:
                    detail = f"补 {len(fix.expected - fix.current)} 个模型"
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

        # 报告口径基于「当前库内绑定」：本轮存在绑定校正时，收缩前的陈旧绑定可能
        # 让「白名单脱节」暂不显形（补齐动作放在绑定校正之后执行，见下方）
        self._report_field_permission_gaps()

        wide_manager = sync.audit_wide_manager_grants()
        if wide_manager:
            self.stdout.write(
                self.style.WARNING(
                    f"[宽授权] {len(wide_manager)} 个（部门管理员, 宽规则）组合：数据权限并集取最宽，"
                    "管理员持有「全部数据」等宽规则时部门边界失效（去数据权限页调整或确认有意为之）"
                )
            )
            for user, rule_name in wide_manager[:DETAIL_LIMIT]:
                self.stdout.write(self.style.WARNING(f"  ~ {user.username} | {rule_name}"))

        if dry_run:
            if options["fix_field_permissions"]:
                self._report_pending_field_nodes()
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

        # 补齐放在绑定校正之后：判定与取值都基于校正后的绑定模型，
        # 避免「陈旧绑定掩盖白名单脱节」（补出的白名单混入失效模型字段）
        if options["fix_field_permissions"]:
            self._fill_field_permissions()

        if options["grant_roles"] and created:
            parent_of_created = {menu.pk: menu.parent_id for menu in created}
            granted = sync.grant_to_roles(created, perms, parent_of_created)
            self.stdout.write(f"[授权] 已为 {len(granted)} 个（角色, 权限点）组合补授")

        if options["update_seed"]:
            # 回写范围 = 新增 + 方法修正 + 绑定校正（三类变更都必须落盘，否则下次
            # load_init_json 会用旧种子把它们覆盖回去）+ 字段权限补齐
            self._update_seed(created, [fix.menu for fix in fixes], method_fixed, options["fix_field_permissions"])

    # ------------------------------------------------------------------ 字段权限

    def _report_field_permission_gaps(self) -> None:
        """审计「已获模型权限点但白名单未覆盖该模型」的角色组合（只报告不落库）。

        字段权限为 fail-closed 的零字段口径：缺口组合的接口输出空对象（列表/详情
        整块空白）。报告把静默空输出变成可见告警；``--fix-field-permissions`` 按
        「权限点绑定模型全字段」补齐，且不改写管理员在角色页已有的收敛配置。
        """
        field_gaps = sync.audit_field_permissions()
        if field_gaps:
            self.stdout.write(
                self.style.WARNING(
                    f"[字段权限] {len(field_gaps)} 个（角色, 权限点）组合的字段白名单未覆盖权限点模型："
                    "这些角色在该接口上输出空对象（零字段 fail-closed，去角色页配置字段白名单）"
                )
            )
            for role, menu in field_gaps[:DETAIL_LIMIT]:
                self.stdout.write(
                    self.style.WARNING(f"  ~ {role.name} | {menu.name} | {menu.method or '-'} {menu.path}")
                )

    def _fill_field_permissions(self) -> None:
        completed = sync.complete_field_permissions()
        self.stdout.write(
            f"[字段权限] 已补齐 {len(completed)} 个（角色, 权限点）组合的字段白名单（只增不减，含新增字段）"
        )

    def _report_pending_field_nodes(self) -> None:
        """dry-run：列出待补的（角色, 权限点）与缺失字段；基于当前库内字段树。"""
        pending = sync.complete_field_permissions(dry_run=True)
        if not pending:
            self.stdout.write(self.style.SUCCESS("[字段权限] 字段白名单完整，无需补齐"))
            return
        self.stdout.write(self.style.WARNING(f"[字段权限] 待补齐 {len(pending)} 个（角色, 权限点）组合："))
        for role, menu, fields in pending[:DETAIL_LIMIT]:
            names = "、".join(field.name for field in fields)
            self.stdout.write(f"  ~ {role.name} | {menu.name} | 缺：{names}")
        if len(pending) > DETAIL_LIMIT:
            self.stdout.write(f"  ... 其余 {len(pending) - DETAIL_LIMIT} 条略")

    # ------------------------------------------------------------------ 种子

    def _update_seed(
        self,
        created_menus: Any,
        fixed_menus: Any,
        method_fixed: Any = None,
        update_field_permissions: Any = False,
    ) -> None:
        """把变更按**应用归属**回写各层种子（平台层 ``loadjson/`` + ``<app>/seeds/``）。

        归属与 ``seed_split`` / ``dump_init_json --split-apps`` 同源（``row_owners``），
        因此写完不需要再跑一次 ``seed_split``；同一层的规范文件名由
        ``write_layer_model`` 统一（并把遗留的按模型拆分文件收敛掉）。
        """

        manifests = discover().manifests
        seed_menu_rows = read_model_rows("system.menu")
        seed_meta_rows = read_model_rows("system.menumeta")
        seed_pks = {str(row["pk"]) for row in seed_menu_rows}

        menus = {
            menu.pk: menu
            for menu in [*created_menus, *fixed_menus, *(method_fixed or [])]
            if not str(menu.pk).startswith(sync.RUNTIME_PERMISSION_PREFIXES)
        }
        # 库内新增但种子缺失的权限点一并补写（多次执行间也能收敛，如先执行修复再补种子）。
        # 限定「父菜单也在种子中」：父菜单不落种子 = 运行时模块自建（演示/插件等，如
        # demo book 的 6eed 段权限点），其权限点由模块自身的种子命令维护，不进种子。
        for menu in Menu.objects.filter(menu_type=Menu.MenuChoices.PERMISSION, deleted_at__isnull=True).exclude(
            pk__in=seed_pks
        ):
            if str(menu.pk).startswith(sync.RUNTIME_PERMISSION_PREFIXES):
                continue
            if menu.parent_id and str(menu.parent_id) in seed_pks:
                menus.setdefault(menu.pk, menu)
        if menus:
            menu_entries = sync.dump_entries(Menu, list(menus.values()))
            meta_entries = sync.dump_entries(MenuMeta, [menu.meta for menu in menus.values()])
            owners = row_owners(
                {
                    "system.menu": _merged_rows(seed_menu_rows, menu_entries),
                    "system.menumeta": _merged_rows(seed_meta_rows, meta_entries),
                },
                manifests,
            )
            self._write_seed_layers(owners["system.menu"], "system.menu", menu_entries, manifests)
            self._write_seed_layers(owners["system.menumeta"], "system.menumeta", meta_entries, manifests)

        field_entries = sync.dump_entries(
            ModelLabelField, ModelLabelField.objects.all(), exclude_fields=("updated_time",)
        )
        field_owners = row_owners(
            {"system.modellabelfield": _merged_rows(read_model_rows("system.modellabelfield"), field_entries)},
            manifests,
        )["system.modellabelfield"]
        self._write_seed_layers(field_owners, "system.modellabelfield", field_entries, manifests)

        if update_field_permissions:
            self._update_field_permission_seed(manifests)

    def _write_seed_layers(
        self,
        owners: dict[str, str],
        model: str,
        entries: list[dict[str, Any]],
        manifests: dict[str, Any],
    ) -> None:
        """按归属把条目分发到各层文件，并登记清单（``merge=True`` 保留同层其它行）。"""

        base = Path(settings.PROJECT_DIR)
        groups: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            app = owners.get(str(entry["pk"]), PLATFORM)
            groups.setdefault(app, []).append(entry)
        for app, rows in sorted(groups.items()):
            path = write_layer_model(base, app, model, rows, manifests=manifests, merge=True)
            label = "平台层" if app == PLATFORM else app
            self.stdout.write(f"[种子] {label} {path.relative_to(base)}（新增/更新 {len(rows)} 行）")

    def _update_field_permission_seed(self, manifests: dict[str, Any]) -> None:
        """字段权限按「种子内角色 × 种子内权限点」整角色回写（跟随权限点所属应用）。

        不依赖本次是否产生补齐（补齐结果必须落盘，否则下次 load_init_json 用旧
        白名单覆盖回缺口）。代码兜底的内置角色（SystemAdmin 等）不在 userrole.json
        内，不回写；运行时模块自建的权限点（demo/插件）不在菜单种子里，
        其白名单由模块自身的种子命令维护。
        """
        from identity.services import UserRole

        seed_role_pks = {row["pk"] for row in read_model_rows("identity.userrole")}
        menu_rows = read_model_rows("system.menu")
        # 运行时模块权限点即使误入种子也不回写白名单（模块自身命令维护）
        seed_menu_pks = {
            str(row["pk"]) for row in menu_rows if not str(row["pk"]).startswith(sync.RUNTIME_PERMISSION_PREFIXES)
        }
        seeded_roles = UserRole.objects.filter(pk__in=seed_role_pks)
        fp_entries = sync.dump_entries(
            FieldPermission,
            FieldPermission.objects.filter(role__in=seeded_roles, menu_id__in=seed_menu_pks),
            exclude_fields=("created_time", "updated_time"),
        )
        owners = row_owners(
            {
                "system.menu": menu_rows,
                "system.fieldpermission": _merged_rows(read_model_rows("system.fieldpermission"), fp_entries),
            },
            manifests,
        )["system.fieldpermission"]
        self._write_seed_layers(owners, "system.fieldpermission", fp_entries, manifests)


def _merged_rows(seed_rows: list[Any], entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """种子行与本次变更按主键合并（本次变更覆盖同名主键，用于归属计算）。"""

    merged: dict[str, dict[str, Any]] = {str(row["pk"]): row for row in seed_rows}
    for entry in entries:
        merged[str(entry["pk"])] = entry
    return list(merged.values())
