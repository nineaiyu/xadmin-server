#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全局搜索提供者注册表（ADR-028，G9）。

口径（逐实体两道门，缺一不可）：
- **页面权限门**：调用者须已拥有该实体页面的 list 权限（按 URL 匹配菜单权限码，与
  `common.core.permission` 同源）——没有页面权限就看不到该分组，搜索不成为绕过页面
  权限的信息通道；
- **数据权限门**：命中关键词的查询集统一过 `get_filter_queryset`（数据权限编译器，
  fail-closed：无适用授权 = 空结果），与各页面列表口径完全一致。

检索策略：基线 `icontains`（全库可移植、中文可用）。关键词为 SQL LIKE 通配符语义
（`%`/`_` 是通配符而非字面量）：Django 的 icontains 不带 ESCAPE 子句，手工转义反而
破坏匹配（已在 E2E 库实证）；通配符只会放大检索范围，不构成注入（参数化）或越权
（权限门在查询集层）。Postgres 全文检索引擎化（zhparser/pg_trgm）登记为部署侧评估
出口：标准 Postgres 镜像无中文分词扩展、内置 simple 解析器对中文不可用，且单实体
规模 + 每组 LIMIT 下 icontains 无性能压力（perf.yml 基线可复核）。
"""

from dataclasses import dataclass
from typing import Any, Callable

from django.db.models import Q, QuerySet

from common.core.filter import get_filter_queryset
from common.core.permission import get_menu_pk, get_user_permission
from system.models import ApprovalRequest, DeptInfo, Leave, OperationLog, UploadFile, UserInfo

KEYWORD_MAX_LENGTH = 50
GROUP_LIMIT = 5


def _match_keyword(queryset: QuerySet, text_fields, keyword: str) -> QuerySet:
    condition = Q()
    for field_name in text_fields:
        condition |= Q(**{f"{field_name}__icontains": keyword})
    return queryset.filter(condition)


def _approval_row_scope(user, queryset: QuerySet) -> QuerySet:
    """审批单行级收紧（与审批中心口径一致）：申请人 = creator，非超管只看与自己相关的单。"""
    if user.is_superuser:
        return queryset
    return queryset.filter(Q(creator=user) | Q(approver=user))


def _leave_row_scope(user, queryset: QuerySet) -> QuerySet:
    """请假单行级收紧（与请假列表口径一致）：我提交 ∪ 我审批过。"""
    if user.is_superuser:
        return queryset
    return queryset.filter(
        Q(creator=user) | Q(instance__tasks__assignee=user) | Q(instance__tasks__actor=user)
    ).distinct()


@dataclass(frozen=True)
class SearchProvider:
    """单个实体的搜索分组定义。"""

    key: str
    label: str
    route: str
    list_url: str
    queryset: Callable[[], QuerySet]
    text_fields: tuple[str, ...]
    display_field: str
    meta_fields: tuple[str, ...] = ()
    limit: int = GROUP_LIMIT
    superuser_only: bool = False
    row_scope: Callable[[Any, QuerySet], QuerySet] | None = None

    def visible_to(self, user, permission_data: dict) -> bool:
        """页面权限门：按 list 权限 URL 匹配用户的菜单权限码集合（超管与全局 URL 门同口径直通）。"""
        if self.superuser_only and not user.is_superuser:
            return False
        if user.is_superuser:
            return True
        return bool(get_menu_pk(permission_data, f"/{self.list_url}"))

    def search(self, user, keyword: str) -> dict | None:
        queryset = _match_keyword(self.queryset(), self.text_fields, keyword)
        if self.row_scope is not None:
            queryset = self.row_scope(user, queryset)
        queryset = get_filter_queryset(queryset, user)
        total = queryset.count()
        if not total:
            return None
        rows = queryset.values("pk", self.display_field, *self.meta_fields)[: self.limit]
        return {
            "key": self.key,
            "label": self.label,
            "route": self.route,
            "total": total,
            "items": [
                {
                    "pk": str(row["pk"]),
                    "text": row[self.display_field] or "",
                    "meta": {name: row[name] for name in self.meta_fields},
                }
                for row in rows
            ],
        }


SEARCH_PROVIDERS = (
    SearchProvider(
        key="user",
        label="用户",
        route="/system/user/index",
        list_url="api/system/user",
        queryset=lambda: UserInfo.objects.all().order_by("username"),
        text_fields=("username", "nickname", "email", "phone"),
        display_field="username",
        meta_fields=("nickname",),
    ),
    SearchProvider(
        key="dept",
        label="部门",
        route="/system/dept/index",
        list_url="api/system/dept",
        queryset=lambda: DeptInfo.objects.all().order_by("name"),
        text_fields=("name", "code"),
        display_field="name",
    ),
    SearchProvider(
        key="file",
        label="文件",
        route="/system/file/index",
        list_url="api/system/file",
        queryset=lambda: UploadFile.objects.all().order_by("-created_time"),
        text_fields=("filename",),
        display_field="filename",
    ),
    SearchProvider(
        key="approval",
        label="审批单",
        route="/system/approval/index",
        list_url="api/system/approvals",
        queryset=lambda: ApprovalRequest.objects.all().order_by("-created_time"),
        text_fields=("path", "module", "object_pk"),
        display_field="path",
        meta_fields=("module", "status"),
        row_scope=_approval_row_scope,
    ),
    SearchProvider(
        key="leave",
        label="请假申请",
        route="/system/leave/index",
        list_url="api/system/leaves",
        queryset=lambda: Leave.objects.select_related("creator").order_by("-created_time"),
        text_fields=("reason",),
        display_field="reason",
        meta_fields=("leave_type", "status"),
        row_scope=_leave_row_scope,
    ),
    SearchProvider(
        key="log",
        label="操作日志",
        route="/system/logs/operation/index",
        list_url="api/system/logs/operation",
        queryset=lambda: OperationLog.objects.all().order_by("-created_time"),
        text_fields=("path", "module", "ipaddress"),
        display_field="path",
        meta_fields=("method", "status_code"),
        superuser_only=True,
    ),
)


def run_global_search(user, keyword: str, scope: str | None = None) -> list[dict]:
    """按注册表逐实体检索，返回有命中的分组（分组的权限门在 provider 内判定）。"""
    keyword = (keyword or "").strip()
    if not keyword or len(keyword) > KEYWORD_MAX_LENGTH:
        return []
    permission_data = get_user_permission(user, "GET")
    groups = []
    for provider in SEARCH_PROVIDERS:
        if scope and provider.key != scope:
            continue
        if not provider.visible_to(user, permission_data):
            continue
        group = provider.search(user, keyword)
        if group:
            groups.append(group)
    return groups
