#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全局搜索提供者注册表。

口径（逐实体两道门，缺一不可）：
- **页面权限门**：调用者须已拥有该实体页面的 list 权限（按 URL 匹配菜单权限码，与
  `common.core.permission` 同源）——没有页面权限就看不到该分组，搜索不成为绕过页面
  权限的信息通道；
- **数据权限门**：命中关键词的查询集统一过 `get_filter_queryset`（数据权限编译器，
  fail-closed：无适用授权 = 空结果），与各页面列表口径完全一致。

检索策略：基线 `icontains`（全库可移植、中文可用）。关键词为 SQL LIKE 通配符语义
（`%`/`_` 是通配符而非字面量）：Django 的 icontains 不带 ESCAPE 子句，手工转义反而
破坏匹配（已在 E2E 库实证）；通配符只会放大检索范围，不构成注入（参数化）或越权
（权限门在查询集层）。PostgreSQL 部署下由 pg_trgm GIN 索引加速前缀通配检索
（清单/豁免/降级语义见 `system/search_indexes.py`，登记于 system/search_indexes.py，
索引快照按表归属拆在 system.0004 与 approval.0001 两个迁移）——**检索语义不变**：非 PG 或扩展不可用时自动回退
顺序扫描，结果与排序口径一致；关键词 ≥2 字符才可能命中索引（单字符无 trigram）。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db.models import Q, QuerySet
from django.utils.translation import gettext_lazy as _

from audit.services import OperationLog
from common.core.filter import get_filter_queryset
from common.core.mask import apply_output_mask
from common.core.permission import get_menu_pk, get_user_field_queryset_for_menus, get_user_permission
from common.settings_contract import kernel_required_setting
from file.services import UploadFile
from identity.services import DeptInfo, Post, UserInfo
from server.utils import get_current_request
from system.models import Menu, Tag

KEYWORD_MAX_LENGTH = 50
GROUP_LIMIT = 5


def _match_keyword(queryset: QuerySet, text_fields: Any, keyword: str) -> QuerySet:
    condition = Q()
    for field_name in text_fields:
        condition |= Q(**{f"{field_name}__icontains": keyword})
    return queryset.filter(condition)


def _approval_queryset() -> Any:
    from approval.models import ApprovalRequest

    return ApprovalRequest.objects.all().order_by("-created_time")


def _leave_queryset() -> Any:
    from approval.models import Leave

    return Leave.objects.select_related("creator").order_by("-created_time")


def _approval_row_scope(user: Any, queryset: QuerySet) -> QuerySet:
    """审批单行级收紧（与审批中心口径一致）：申请人 = creator，非超管只看与自己相关的单。"""
    if user.is_superuser:
        return queryset
    return queryset.filter(Q(creator=user) | Q(approver=user))


def _leave_row_scope(user: Any, queryset: QuerySet) -> QuerySet:
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
    #: 公共字典类资源（如标签中心）：页面列表已显式剥离数据权限，搜索必须同口径
    skip_data_permission: bool = False
    # 命中跳转承接字段：分组路由对应列表页的搜索字段名（RePlusPage 的
    # route.query → 搜索默认值装配）。跳转带 ?<search_field>=<关键词> 让列表页
    # 直接过滤到命中；None = 列表页无对齐搜索字段，跳转不带 query
    search_field: str | None = None

    def visible_to(self, user: Any, permission_data: dict[str, Any]) -> bool:
        """页面权限门：按 list 权限 URL 匹配用户的菜单权限码集合（超管与全局 URL 门同口径直通）。"""
        if self.superuser_only and not user.is_superuser:
            return False
        if user.is_superuser:
            return True
        return bool(get_menu_pk(permission_data, f"/{self.list_url}"))

    def _allowed_fields(self, user: Any, permission_data: Any) -> set[Any] | None:
        """本组可读字段（字段权限白名单）；None = 不受限（超管 / 字段权限关闭）。

        搜索此前只套数据权限与脱敏、不套字段权限：无字段权限的角色在列表拿到
        空对象，搜索仍返回 username/nickname——隐藏字段还能当存在性预言机。
        """
        if user.is_superuser or not kernel_required_setting("PERMISSION_FIELD_ENABLED"):
            return None
        data = permission_data if permission_data is not None else get_user_permission(user, "GET")
        menu_data = get_menu_pk(data, f"/{self.list_url}")
        if not menu_data:
            return set()
        # get_menu_pk 返回 (pk, model)；按权限点主键取菜单（字段白名单的归属维度）
        menus = list(Menu.objects.filter(pk=menu_data[0]))
        fields = get_user_field_queryset_for_menus(user, menus)
        return set(fields.get(self.queryset().model._meta.label_lower, set()))

    def search(
        self, user: Any, keyword: str, request: Any = None, permission_data: Any = None
    ) -> dict[str, Any] | None:
        allowed = self._allowed_fields(user, permission_data)
        if allowed is not None:
            # 显示字段不可见时整组跳过：只返回空 text / 其它元信息的命中
            # 仍会泄露「存在该记录」这一事实
            if self.display_field not in allowed:
                return None
            visible_meta = tuple(name for name in self.meta_fields if name in allowed)
            visible_text = tuple(name for name in self.text_fields if name in allowed)
            if not visible_text:
                return None
        else:
            visible_meta = self.meta_fields
            visible_text = self.text_fields
        queryset = _match_keyword(self.queryset(), visible_text, keyword)
        if self.row_scope is not None:
            queryset = self.row_scope(user, queryset)
        if not self.skip_data_permission:
            queryset = get_filter_queryset(queryset, user)
        # 先取「限值 + 1」行：未饱和时条数即总数（省掉一次全量 COUNT，全局搜索 8 个
        # provider 每轮少 8 次计数查询）；只有饱和（结果 ≥ limit）才补一次 COUNT 取真实总数
        rows = list(queryset.values("pk", self.display_field, *visible_meta)[: self.limit + 1])
        if not rows:
            return None
        if len(rows) <= self.limit:
            total = len(rows)
        else:
            rows = rows[: self.limit]
            total = queryset.count()
        items = [
            {
                "pk": str(row["pk"]),
                "text": row[self.display_field] or "",
                "meta": {name: row[name] for name in visible_meta},
            }
            for row in rows
        ]
        return {
            "key": self.key,
            "label": self.label,
            "route": self.route,
            "search_field": self.search_field,
            "total": total,
            "items": self._mask_items(items, queryset.model, request, user, visible_meta),
        }

    def _mask_items(self, items: Any, model: Any, request: Any, user: Any, meta_fields: Any = None) -> list[Any]:
        """分组输出过脱敏规则（与列表接口同源，防「列表已掩码、搜索仍原文」旁路）。

        取值维度与声明一致：``text`` 对应 display_field、``meta`` 对应 meta_fields，
        合并为字段名字典后统一掩码再拆回（字段名与模型不一致时不会被规则命中）。
        """
        if not items:
            typed_value: list[Any] = items
            return typed_value
        fields = self.meta_fields if meta_fields is None else meta_fields
        for item in items:
            flat = {self.display_field: item["text"], **item["meta"]}
            apply_output_mask(flat, request, user, model)
            item["text"] = flat.get(self.display_field) or ""
            item["meta"] = {name: flat.get(name) for name in fields}
        masked_items: list[Any] = items
        return masked_items


# 内置实体清单（运行期可通过 register_search_provider 追加业务模型分组）
SEARCH_PROVIDERS: list[SearchProvider] = [
    SearchProvider(
        key="user",
        label=_("User"),
        route="/system/user/index",
        list_url="api/identity/user",
        queryset=lambda: UserInfo.objects.all().order_by("username"),
        text_fields=("username", "nickname", "email", "phone"),
        display_field="username",
        meta_fields=("nickname",),
        search_field="username",
    ),
    SearchProvider(
        key="dept",
        label=_("Departments"),
        route="/system/dept/index",
        list_url="api/identity/dept",
        queryset=lambda: DeptInfo.objects.all().order_by("name"),
        text_fields=("name", "code"),
        display_field="name",
        search_field="name",
    ),
    SearchProvider(
        key="post",
        label=_("Posts"),
        route="/system/post/index",
        list_url="api/identity/posts",
        queryset=lambda: Post.objects.all().order_by("name"),
        text_fields=("name", "code"),
        display_field="name",
        meta_fields=("code",),
        search_field="name",
    ),
    SearchProvider(
        key="file",
        label=_("Files"),
        route="/system/file/index",
        list_url="api/file/file",
        queryset=lambda: UploadFile.objects.all().order_by("-created_time"),
        text_fields=("filename",),
        display_field="filename",
        search_field="filename",
    ),
    SearchProvider(
        key="tag",
        label=_("Tags"),
        route="/system/tag/index",
        list_url="api/system/tags",
        queryset=lambda: Tag.objects.all().order_by("name"),
        skip_data_permission=True,
        text_fields=("name", "remark"),
        display_field="name",
        search_field="name",
    ),
    SearchProvider(
        key="approval",
        label=_("Approval requests"),
        route="/approval/index",
        list_url="api/approval/approvals",
        queryset=_approval_queryset,
        text_fields=("path", "module", "object_pk"),
        display_field="path",
        meta_fields=("module", "status"),
        row_scope=_approval_row_scope,
    ),
    SearchProvider(
        key="leave",
        label=_("Leave requests"),
        route="/approval/leave/index",
        list_url="api/approval/leaves",
        queryset=_leave_queryset,
        text_fields=("reason",),
        display_field="reason",
        meta_fields=("leave_type", "status"),
        row_scope=_leave_row_scope,
    ),
    SearchProvider(
        key="log",
        label=_("Operation logs"),
        route="/system/logs/operation/index",
        list_url="api/audit/logs/operation",
        queryset=lambda: OperationLog.objects.all().order_by("-created_time"),
        text_fields=("path", "module", "ipaddress"),
        display_field="path",
        meta_fields=("method", "status_code"),
        superuser_only=True,
    ),
]


def register_search_provider(provider: SearchProvider) -> SearchProvider:
    """二开扩展点：注册业务模型的搜索分组（AppConfig.ready 等导入期调用）。

    两道权限门口径与内置实体一致（页面权限门按 list_url 匹配、数据权限门
    get_filter_queryset fail-closed）；trgm 索引登记清单不含二开实体（小规模
    顺序扫描可接受，量级上来后按 system/search_indexes.py 的清单口径补登记）。
    key 冲突 fail-fast，防止静默覆盖内置分组。
    """
    if any(item.key == provider.key for item in SEARCH_PROVIDERS):
        raise ValueError(f"duplicate global search provider key: {provider.key}")
    SEARCH_PROVIDERS.append(provider)
    return provider


def run_global_search(user: Any, keyword: str, scope: str | None = None) -> list[dict[str, Any]]:
    """按注册表逐实体检索，返回有命中的分组（分组的权限门在 provider 内判定）。"""
    keyword = (keyword or "").strip()
    if not keyword or len(keyword) > KEYWORD_MAX_LENGTH:
        return []
    permission_data = get_user_permission(user, "GET")
    # 输出脱敏需要请求上下文（超管/原文通道豁免判定）；无上下文时按脱敏口径输出
    request = get_current_request()
    groups = []
    for provider in SEARCH_PROVIDERS:
        if scope and provider.key != scope:
            continue
        if not provider.visible_to(user, permission_data):
            continue
        group = provider.search(user, keyword, request, permission_data=permission_data)
        if group:
            groups.append(group)
    return groups
