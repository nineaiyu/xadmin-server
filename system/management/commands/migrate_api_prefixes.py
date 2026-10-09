#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""API 前缀平移（存量库）：把 ``/api/system/<域>`` 的持久化引用改写为独立域前缀。

背景：identity / file / audit / task 四域自 system app 拆出后，路由前缀与 app
边界对齐（``/api/identity/``、``/api/file/``、``/api/audit/``、``/api/task/``）。
代码与种子面已同步平移，本命令负责**存量库**里持久化的路径引用：

- ``Menu.path``（权限点）：``api/system/user$`` → ``api/identity/user$``；
- ``PersonalAccessToken`` / ``ApiApplication`` / ``OAuthRefreshToken`` 的 ``scopes``：
  锚定正则条目（``GET ^/api/system/user/?$``）按域前缀改写；
- ``ApprovalRule.path_patterns``：审批规则路径正则清单；
- ``SysConfig`` 的 ``APPROVAL_REQUIRED_PATHS``：操作审批必需路径正则清单。

幂等：仅命中「旧前缀 + 四域段」的条目改写，重复执行零副作用；缺省 dry-run
只报告，``--apply`` 落库。system 内核域路径（menu / permission / dict /
config / dashboard / monitor / tags / codegen / credentials / modules 等）
不在映射表内，原样保留。

用法::

    python manage.py migrate_api_prefixes            # dry-run（只报告）
    python manage.py migrate_api_prefixes --apply    # 落库
"""

from typing import Any

from django.core.management.base import BaseCommand

#: 旧前缀 → 新前缀（顺序敏感：长前缀在前——user/log 归 audit 域须先于 user，
#: login-policies 须先于 login，userinfo 须先于 user）
PREFIX_MAP: tuple[tuple[str, str], ...] = (
    ("api/system/user/log", "api/audit/user/log"),
    ("api/system/logs/", "api/audit/logs/"),
    ("api/system/mask-rules", "api/audit/mask-rules"),
    ("api/system/import-templates", "api/task/import-templates"),
    ("api/system/imports", "api/task/imports"),
    ("api/system/exports", "api/task/exports"),
    ("api/system/tasks/", "api/task/"),
    ("api/system/tasks$", "api/task$"),
    ("api/system/webhooks/", "api/task/webhooks/"),
    ("api/system/file", "api/file/file"),
    ("api/system/userinfo", "api/identity/userinfo"),
    ("api/system/user", "api/identity/user"),
    ("api/system/dept", "api/identity/dept"),
    ("api/system/posts", "api/identity/posts"),
    ("api/system/role", "api/identity/role"),
    ("api/system/online", "api/identity/online"),
    ("api/system/directory", "api/identity/directory"),
    ("api/system/personal-access-tokens", "api/identity/personal-access-tokens"),
    ("api/system/api-applications", "api/identity/api-applications"),
    ("api/system/account-risks", "api/identity/account-risks"),
    ("api/system/login-policies", "api/identity/login-policies"),
    ("api/system/passkeys", "api/identity/passkeys"),
    ("api/system/search/user", "api/identity/search/user"),
    ("api/system/search/role", "api/identity/search/role"),
    ("api/system/search/dept", "api/identity/search/dept"),
    ("api/system/search/post", "api/identity/search/post"),
    ("api/system/login", "api/identity/login"),
    ("api/system/auth/", "api/identity/auth/"),
    ("api/system/logout", "api/identity/logout"),
    ("api/system/refresh", "api/identity/refresh"),
    ("api/system/register", "api/identity/register"),
    ("api/system/rules/password", "api/identity/rules/password"),
    ("api/system/impersonate/exit", "api/identity/impersonate/exit"),
    ("api/system/open/", "api/identity/open/"),
)


def translate_text(value: str) -> str | None:
    """按映射表改写字符串中的域前缀；未命中返回 None（保持原值）。"""
    for old, new in PREFIX_MAP:
        if old in value:
            return value.replace(old, new)
    return None


def translate_list(values: Any) -> tuple[list[Any], int]:
    """改写字符串清单里的域前缀；返回 (新清单, 命中条数)。"""
    items = list(values or [])
    changed = 0
    for index, item in enumerate(items):
        if not isinstance(item, str):
            continue
        new = translate_text(item)
        if new is not None:
            items[index] = new
            changed += 1
    return items, changed


class Command(BaseCommand):
    help = "API 前缀平移（存量库）：/api/system/<四域> → /api/identity|file|audit|task（幂等，默认 dry-run）"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--apply", action="store_true", help="落库（缺省只报告，不写入）")

    def handle(self, *args: Any, **options: Any) -> None:
        apply = bool(options["apply"])
        # 跨 app 消费一律函数内延迟导入（内核命令对业务 app 无模块级依赖）
        from approval.models.approval_rule import ApprovalRule
        from common.core.config import SysConfig
        from identity.models.token import ApiApplication, OAuthRefreshToken, PersonalAccessToken
        from system.models import Menu

        total = 0

        # 1) 菜单权限点 path（唯一索引字段，逐条可回滚改写）
        for menu in Menu.objects.filter(path__startswith="api/system/").order_by("pk"):
            new = translate_text(str(menu.path))
            if new is None:
                continue
            total += 1
            self.stdout.write(f"  Menu[{menu.pk}] {menu.path} -> {new}")
            if apply:
                menu.path = new
                menu.save(update_fields=["path"])

        # 2) 令牌 / 应用 / 刷新令牌的接口范围（JSON 正则清单）
        for model in (PersonalAccessToken, ApiApplication, OAuthRefreshToken):
            for row in model.objects.exclude(scopes=[]).order_by("pk"):
                new_items, changed = translate_list(row.scopes)
                if not changed:
                    continue
                total += changed
                self.stdout.write(f"  {model.__name__}[{row.pk}] {row.scopes} -> {new_items}")
                if apply:
                    row.scopes = new_items
                    row.save(update_fields=["scopes"])

        # 3) 审批规则的路径正则清单
        for rule in ApprovalRule.objects.exclude(path_patterns=[]).order_by("pk"):
            new_items, changed = translate_list(rule.path_patterns)
            if not changed:
                continue
            total += changed
            self.stdout.write(f"  ApprovalRule[{rule.pk}] {rule.path_patterns} -> {new_items}")
            if apply:
                rule.path_patterns = new_items
                rule.save(update_fields=["path_patterns"])

        # 4) 操作审批必需路径（系统配置）
        required_paths = [str(item) for item in (SysConfig.APPROVAL_REQUIRED_PATHS or []) if item]
        new_paths, changed = translate_list(required_paths)
        if changed:
            total += changed
            self.stdout.write(f"  SysConfig.APPROVAL_REQUIRED_PATHS {required_paths} -> {new_paths}")
            if apply:
                SysConfig.set_value("APPROVAL_REQUIRED_PATHS", new_paths)

        action = "已落库" if apply else "dry-run（未写入）"
        self.stdout.write(self.style.SUCCESS(f"API 前缀平移完成：命中 {total} 条，{action}"))
