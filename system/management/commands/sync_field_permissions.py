#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""新增模型字段后的字段权限一键同步（二开友好入口）。

字段权限是 fail-closed 的：新字段没有进入字段树 / 角色白名单时，非超管页面会
静默缺列。本命令把三件事收口为一条：

1. 重建字段树（``sync_model_field``，按序列化器收集模型字段）；
2. 只把**本次新增**的字段补进已有角色白名单（不触碰历史字段与管理员裁剪）；
3. 可选地把字段树与白名单按应用归属回写各层种子。

用法：
    python manage.py sync_field_permissions --dry-run      # 只报告本次会补哪些字段
    python manage.py sync_field_permissions                # 同步字段树 + 补齐白名单
    python manage.py sync_field_permissions --update-seed  # 同时回写 <app>/seeds
    python manage.py sync_field_permissions --update-seed --app notifications  # 只回写指定应用

安全边界：不做「缺什么补什么」的全量补齐——那会把历史上被管理员裁剪掉的字段
（如用户密码、超管标记）重新授予。新增字段的识别以「同步前字段树快照」为基线；
角色对某模型原本没有任何字段时不自动扩权。执行后重启服务进程。
"""

from typing import Any

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db import transaction

from system.services.modelfield import sync_model_field
from system.services.permission_sync import complete_field_permissions, snapshot_role_field_names
from system.services.permission_sync.seed import write_field_seed_layers

DETAIL_LIMIT = 100


class Command(BaseCommand):
    help = "同步模型字段树与角色字段白名单（新增字段后执行；只补本次新增字段）"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="只报告本次新增字段会补到哪些（角色, 权限点），不写库",
        )
        parser.add_argument(
            "--update-seed",
            action="store_true",
            help="把字段树与字段白名单按应用归属回写各层种子",
        )
        parser.add_argument(
            "--app",
            default="",
            help="配合 --update-seed：只回写这些应用（逗号分隔；默认全部）",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["dry_run"]:
            self._report_pending()
            return

        baseline = snapshot_role_field_names()
        tree = sync_model_field()
        role_tree, data_tree = tree["role"], tree["data"]
        self.stdout.write(
            f"[字段] 角色字段树 {role_tree['kept']} 项（清理 {role_tree['deleted']}）；"
            f"数据权限字段树 {data_tree['kept']} 项（清理 {data_tree['deleted']}）"
        )

        completed = complete_field_permissions(only_new=True, baseline=baseline)
        if completed:
            self.stdout.write(f"[字段权限] 已补齐 {len(completed)} 个（角色, 权限点）组合的新增字段（只增不减）")
            self._write_details(completed)
        else:
            self.stdout.write("[字段权限] 没有需要补白名单的新增字段（历史字段与人工裁剪保持不变）")

        if options["update_seed"]:
            apps = [item.strip() for item in str(options["app"] or "").split(",") if item.strip()] or None
            written = write_field_seed_layers(apps=apps)
            for layer, path, count in written:
                label = "平台层" if layer == "platform" else layer
                self.stdout.write(f"[种子] {label} {path}（{count} 行）")

        cache.clear()
        self.stdout.write(
            self.style.SUCCESS("[字段权限] 同步完成，已清空元数据缓存；重启服务进程后前端即可看到新字段。")
        )

    def _report_pending(self) -> None:
        # 在事务里真实重建一次字段树再回滚：既拿到准确的「新增字段」基线差异，
        # 又不写库（sync_model_field 内部是 atomic，可嵌套）。
        with transaction.atomic():
            baseline = snapshot_role_field_names()
            sync_model_field()
            pending = complete_field_permissions(only_new=True, baseline=baseline, dry_run=True)
            transaction.set_rollback(True)

        if not pending:
            self.stdout.write(self.style.SUCCESS("[字段权限] 没有新增字段需要补白名单"))
            return
        self.stdout.write(self.style.WARNING(f"[字段权限] 本次将补齐 {len(pending)} 个（角色, 权限点）组合："))
        self._write_details(pending)

    def _write_details(self, entries: Any) -> None:
        for role, menu, fields in entries[:DETAIL_LIMIT]:
            names = "、".join(field.name for field in fields)
            self.stdout.write(f"  ~ {role.name} | {menu.name} | 新增：{names}")
        if len(entries) > DETAIL_LIMIT:
            self.stdout.write(f"  ... 其余 {len(entries) - DETAIL_LIMIT} 条略")
