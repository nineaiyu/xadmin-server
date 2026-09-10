#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : audit_data_permission_rules
"""存量数据权限规则巡检。

读取侧对坏规则已 fail-closed（编译为全拒绝 + 告警），但「用户看到空集」无直接线索。
本命令遍历全部 DataPermission.rules，用与写入侧同一套 validate_rules 校验，
列出每条非法授权（含具体规则与失败原因），便于定位与修正。

用法：
    python manage.py audit_data_permission_rules              # 只列出，不改库
    python manage.py audit_data_permission_rules --deactivate # 非法授权整体停用（is_active=False）
    python manage.py audit_data_permission_rules --strict     # 发现非法即以非零退出（CI 门禁）

注意：--deactivate 不改写 rules JSON 本身（避免误改数据），停用后管理员可在
页面上修正规则后重新启用。
"""

from django.core.management.base import BaseCommand
from rest_framework.exceptions import ValidationError

from common.core.data_scope import validate_rules
from common.utils import get_logger
from system.models import DataPermission

logger = get_logger(__name__)


def _format_detail(exc: ValidationError) -> str:
    detail = exc.detail
    if isinstance(detail, (list, tuple)):
        return "; ".join(str(item) for item in detail)
    return str(detail)


class Command(BaseCommand):
    help = "Audit stored data permission rules against the write-side validator"

    def add_arguments(self, parser):
        parser.add_argument("--deactivate", action="store_true", help="Deactivate invalid permissions after listing")
        parser.add_argument(
            "--strict", action="store_true", help="Exit with a non-zero code when invalid permissions are found"
        )

    def handle(self, *args, **options):
        deactivate = options["deactivate"]
        strict = options["strict"]
        invalid = []
        for dp in DataPermission.objects.all().order_by("created_time"):
            try:
                validate_rules(dp.rules or [])
            except ValidationError as exc:
                invalid.append((dp, _format_detail(exc)))

        if not invalid:
            self.stdout.write("all data permission rules are valid")
            return

        for dp, reason in invalid:
            bound = list(dp.userinfo_set.values_list("username", flat=True)) + [
                f"dept:{name}" for name in dp.deptinfo_set.values_list("name", flat=True)
            ]
            self.stdout.write(
                f"[INVALID] {dp.name} (pk={dp.pk})\n"
                f"  reason: {reason}\n"
                f"  rules: {dp.rules}\n"
                f"  bound to: {', '.join(bound) or '(unbound)'}"
            )
            if deactivate:
                dp.is_active = False
                dp.save(update_fields=["is_active"])
                self.stdout.write("  -> deactivated")

        summary = f"{len(invalid)} invalid permission(s) found"
        if deactivate:
            summary += ", all deactivated"
        self.stdout.write(self.style.WARNING(summary))
        logger.warning("audit data permission rules. %s", summary)
        if strict:
            # CI 门禁：发现非法规则即非零退出（--deactivate 修好后再跑即为 0）
            raise SystemExit(1)
