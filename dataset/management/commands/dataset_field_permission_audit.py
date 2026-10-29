#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审计共享数据集的字段权限准备度（strict 模式迁移前运行）。"""

from typing import Any

from django.core.management.base import BaseCommand

from dataset.models import Dataset


class Command(BaseCommand):
    help = "List shared datasets whose execute-permitted roles have no field permission config"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--fail", action="store_true", help="存在缺口时以非零码退出（CI/发布门禁用）")

    def handle(self, *args: Any, **options: Any) -> None:
        from identity.models import UserRole
        from system.models import FieldPermission

        roles = list(
            UserRole.objects.filter(
                is_active=True,
                deleted_at__isnull=True,
                menu__name="execute:DataDataset",
                menu__deleted_at__isnull=True,
            ).distinct()
        )
        datasets = list(Dataset.objects.filter(visibility=Dataset.Visibility.SHARED).order_by("name"))
        gaps = 0
        for dataset in datasets:
            missing = [
                role.code
                for role in roles
                if not FieldPermission.objects.filter(role=role, field__parent__name=dataset.bound_model).exists()
            ]
            if not missing:
                continue
            gaps += 1
            self.stdout.write(
                self.style.WARNING(
                    f"[GAP] {dataset.name} mode={dataset.field_permission_mode} "
                    f"model={dataset.bound_model} roles={','.join(missing)}"
                )
            )
        if gaps:
            self.stdout.write(
                self.style.WARNING(
                    f"共 {gaps} 个共享数据集存在未配置字段权限的执行角色；"
                    "切换 strict 前请补齐 FieldPermission 或调整数据集列（--fail 可用于发布门禁）"
                )
            )
            if options["fail"]:
                raise SystemExit(1)
        else:
            self.stdout.write(self.style.SUCCESS("共享数据集字段权限巡检通过"))
