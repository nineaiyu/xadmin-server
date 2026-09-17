#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : audit_data_permission_rules
"""存量数据权限规则巡检。

读取侧对坏规则已 fail-closed（编译为全拒绝 + 告警），但「用户看到空集」无直接线索。
本命令做两层体检，覆盖「规则存储格式」与「绑定/引用关系」两个层面：

1. 非法授权（``[INVALID]``，影响退出码）：与写入侧同一套 validate_rules 校验，
   列出每条非法授权（含具体规则与失败原因）；
2. 不生效提示（``[WARN]``，不影响退出码）：规则本身合法，但按当前绑定与引用关系
   对绑定对象恒为空集——未绑定任何用户/部门；「主管部门」类规则绑定对象中没有任何
   部门主管；指定对象（用户/部门/角色/菜单）已被删除，规则可能不再命中任何数据。

用法：
    python manage.py audit_data_permission_rules              # 只列出，不改库
    python manage.py audit_data_permission_rules --deactivate # 非法授权整体停用（is_active=False）
    python manage.py audit_data_permission_rules --strict     # 发现非法即以非零退出（CI 门禁）

注意：``--deactivate`` 只停用非法授权、不改写 rules JSON 本身（避免误改数据），停用后
管理员可在页面上修正规则后重新启用；``[WARN]`` 是配置提示（部分可能是有意为之），只列不改。
"""

from django.core.management.base import BaseCommand
from rest_framework.exceptions import ValidationError

from common.core.data_scope import KeyChoices, validate_rules

# _pk_list 是规则 value 的存储形态归一（JSON 字符串 / {"pk": ...} 字典 / 标量单值），
# 与编译器读侧同源——巡检若自写一份解析，会与运行时对同一份数据产生两种口径
from common.core.data_scope.values import _pk_list
from common.utils import get_logger
from system.models import DataPermission, DeptInfo, Menu, UserInfo, UserRole

logger = get_logger(__name__)

# 「指定对象」类规则的 value → 引用目标模型（悬空引用提示）
REFERENCE_MODELS = {
    KeyChoices.TABLE_USER: UserInfo,
    KeyChoices.TABLE_DEPT: DeptInfo,
    KeyChoices.TABLE_ROLE: UserRole,
    KeyChoices.TABLE_MENU: Menu,
}

# 「主管部门」类规则：按绑定用户的 leader 职责注入部门/成员，无主管职责即恒为空集
LEADER_TYPES = {KeyChoices.LEADER_DEPARTMENTS, KeyChoices.LEADER_USERS}


def _format_detail(exc: ValidationError) -> str:
    detail = exc.detail
    if isinstance(detail, (list, tuple)):
        return "; ".join(str(item) for item in detail)
    return str(detail)


def _bound_text(dp: DataPermission) -> str:
    bound = list(dp.userinfo_set.values_list("username", flat=True)) + [
        f"dept:{name}" for name in dp.deptinfo_set.values_list("name", flat=True)
    ]
    return ", ".join(bound) or "(unbound)"


def _bound_user_pks(dp: DataPermission) -> set:
    """绑定对象覆盖的用户主键：显式绑定用户 + 绑定部门（含全部下级）的成员。"""
    user_pks = set(dp.userinfo_set.values_list("pk", flat=True))
    for dept_pk in dp.deptinfo_set.values_list("pk", flat=True):
        descendants = [str(pk) for pk in DeptInfo.recursion_dept_info(str(dept_pk))]
        user_pks |= set(UserInfo.objects.filter(dept__in=descendants).values_list("pk", flat=True))
    return user_pks


def _ineffective_warnings(dp: DataPermission) -> list:
    """规则合法但对绑定对象恒为空集的配置提示（warning 级，只列不改）。"""
    rules = [rule for rule in (dp.rules or []) if isinstance(rule, dict)]
    warnings = []
    if not dp.userinfo_set.exists() and not dp.deptinfo_set.exists():
        warnings.append("未绑定任何用户或部门（不会对任何人生效）")

    if any(rule.get("type") in LEADER_TYPES for rule in rules):
        bound_pks = _bound_user_pks(dp)
        if bound_pks and not UserInfo.objects.filter(pk__in=bound_pks, leader_depts__is_active=True).exists():
            warnings.append("「主管部门」类规则：绑定对象中没有任何部门主管，规则对所有绑定用户恒为空集")

    for rule in rules:
        model = REFERENCE_MODELS.get(rule.get("type"))
        if model is None:
            continue
        pks = [str(pk) for pk in _pk_list(rule.get("value"))]
        existing = {str(pk) for pk in model.objects.filter(pk__in=pks).values_list("pk", flat=True)}
        missing = [pk for pk in pks if pk not in existing]
        if missing:
            warnings.append(f"规则引用的对象已不存在（{', '.join(missing[:5])}），若无数据引用这些主键将恒为空集")
    return warnings


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
        warnings = []
        for dp in DataPermission.objects.all().order_by("created_time"):
            try:
                validate_rules(dp.rules or [])
            except ValidationError as exc:
                invalid.append((dp, _format_detail(exc)))
                # 非法规则不再做不生效体检：修正规则前，绑定/引用提示没有意义
                continue
            warnings.extend((dp, reason) for reason in _ineffective_warnings(dp))

        for dp, reason in invalid:
            self.stdout.write(
                f"[INVALID] {dp.name} (pk={dp.pk})\n  reason: {reason}\n  rules: {dp.rules}\n  bound to: {_bound_text(dp)}"
            )
            if deactivate:
                dp.is_active = False
                dp.save(update_fields=["is_active"])
                self.stdout.write("  -> deactivated")

        for dp, reason in warnings:
            self.stdout.write(
                f"[WARN] {dp.name} (pk={dp.pk}, is_active={dp.is_active})\n  - {reason}\n  bound to: {_bound_text(dp)}"
            )

        if not invalid:
            self.stdout.write("all data permission rules are valid")
        summary = f"{len(invalid)} invalid permission(s) found"
        if deactivate and invalid:
            summary += ", all deactivated"
        if invalid:
            self.stdout.write(self.style.WARNING(summary))
        if warnings:
            self.stdout.write(
                self.style.WARNING(f"{len(warnings)} ineffective configuration warning(s) listed (not blocking)")
            )
        if invalid:
            logger.warning("audit data permission rules. %s", summary)
        if strict and invalid:
            # CI 门禁：发现非法规则即非零退出（--deactivate 修好后再跑即为 0）；[WARN] 不参与判定
            raise SystemExit(1)
