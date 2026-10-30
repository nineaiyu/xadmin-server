#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""在线用户管理：统一会话查询（WS + 纯 HTTP）+ 按 channel / 按用户强制下线。

历史边界：在线判定依赖 WS 心跳（Redis ZSET），未建 WS 的纯 HTTP 会话不可枚举。
现改为 UserSession 统一数据源（登录即登记）：
- WS 会话（channel_name 非空）：在线判定 = channel 仍在心跳 ZSET 存活（同旧口径）；
- HTTP 会话（channel_name 为空）：在线判定 = last_active 在活跃窗口内
  （认证链路节流刷新，SysConfig.SESSION_ONLINE_TIMEOUT，默认 300s）。
"""

import uuid
from datetime import timedelta
from typing import Any

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied

from common.core.config import SysConfig
from common.core.filter import BaseFilterSet, PkMultipleFilter, get_filter_queryset
from common.core.modelset import ListDeleteModelSet, OnlyExportDataAction
from common.core.pagination import DynamicPageNumber
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from identity.models import UserInfo, UserSession
from identity.serializers.session import UserSessionSerializer
from identity.utils.session import filter_force_logout_targets, force_logout_user, force_logout_users
from message.services import get_online_info, send_logout_msg


class UserOnlineFilter(BaseFilterSet):
    creator_id = PkMultipleFilter(input_type="api-search-user")

    class Meta:
        model = UserSession
        fields = ["creator_id", "login_type"]


class UserOnlineViewSet(ListDeleteModelSet, OnlyExportDataAction):
    """在线用户会话

    WS 连接 + 纯 HTTP 会话统一视图。
    """

    queryset = UserSession.objects.all()
    serializer_class = UserSessionSerializer
    pagination_class = DynamicPageNumber(100)
    filterset_class = UserOnlineFilter
    ordering_fields = ["last_active", "created_time"]
    # 行/批量下线要读会话所属用户的超管标记，避免逐行查询
    select_related_fields = ("creator",)

    def get_queryset(self) -> Any:
        online_user_pks, online_user_sockets = get_online_info()
        cutoff = timezone.now() - timedelta(seconds=SysConfig.SESSION_ONLINE_TIMEOUT)
        # WS 会话看 channel 存活；HTTP 会话看活跃窗口（status=ONLINE 过滤已下线记录）
        return self.queryset.filter(status=UserSession.Status.ONLINE).filter(
            Q(channel_name__in=online_user_sockets) | Q(channel_name="", last_active__gte=cutoff)
        )

    def _ensure_target_logout_allowed(self, creator: Any) -> None:
        request = getattr(self, "request", None)
        # 无请求上下文 = 进程内直接调用（单测/内部清理），不属于 HTTP 越权面
        if request is None:
            return
        if creator is not None and creator.is_superuser and not request.user.is_superuser:
            raise PermissionDenied(_("Cannot force logout a superuser"))

    def _scoped_user_pks(self, user_pks: set[int]) -> tuple[set[int], set[int]]:
        """按数据权限收敛目标用户：返回 (可见, 越界)。超管不受限。"""
        request = getattr(self, "request", None)
        if request is None or request.user.is_superuser or not user_pks:
            return set(user_pks), set()
        scoped = get_filter_queryset(UserSession.objects.filter(creator_id__in=user_pks), request.user)
        visible = set(scoped.values_list("creator_id", flat=True))
        return visible, set(user_pks) - visible

    def _scoped_session_pks(self, session_pks: list[str]) -> tuple[set[int], list[str]]:
        """会话行同样按数据权限收敛；返回 (可处理会话的所属用户, 越界/不存在的会话 pk)。"""
        if not session_pks:
            return set(), []
        request = getattr(self, "request", None)
        rows = dict(UserSession.objects.filter(pk__in=session_pks).values_list("pk", "creator_id"))
        found = {str(pk): creator for pk, creator in rows.items()}
        denied = [pk for pk in session_pks if pk not in found]
        if request is not None and not request.user.is_superuser and found:
            scoped = get_filter_queryset(UserSession.objects.filter(pk__in=list(found)), request.user)
            visible = {str(pk) for pk in scoped.values_list("pk", flat=True)}
            denied.extend(pk for pk in found if pk not in visible)
            found = {pk: creator for pk, creator in found.items() if pk in visible}
        return set(found.values()), denied

    def perform_destroy(self, instance: Any) -> Any:
        """行维度「下线」：WS 会话踢 channel；HTTP 会话按 sid 服务端失效 token。"""
        # 批量删除逐行调用：越权目标在这里抛错，由 BatchDestroyAction 记入 failures
        self._ensure_target_logout_allowed(instance.creator)
        if instance.channel_name:
            send_logout_msg(instance.creator_id, [instance.channel_name])
        else:
            from common.cache.storage import SessionTokenRevokedCache

            # 精确踢本会话：登录时写入的 sid claim，refresh 续命同样失效，
            # 不影响该用户其他在用登录（与用户级 UserTokenRevokedCache 互补）
            SessionTokenRevokedCache(instance.pk).set_storage_cache(1)
        instance.mark_offline()
        return True

    def _needs_rowwise_delete(self) -> Any:
        """批量删除必须逐行：WS 踢线与 HTTP 会话令牌失效只在 perform_destroy 中，
        非逐行分支的 queryset.delete() 会静默跳过这些副作用（在线用户看似被删除、实际未下线）。"""
        return True

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"user_pk": build_basic_type(OpenApiTypes.NUMBER)},
                required=["user_pk"],
                description="用户主键（踢该用户全部会话，不是会话行主键）",
            )
        ),
        responses=get_default_response_schema({"channels": build_basic_type(OpenApiTypes.NUMBER)}),
    )
    @action(methods=["post"], detail=False, url_path="force-logout")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def force_logout(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """强制下线该用户全部会话

        显式 user_pk 契约。服务端令牌失效 + WS 踢线。URL 不再把用户主键挂在会话资源下，
        避免与行维度的单会话下线（destroy）混淆。
        """
        raw_pk = request.data.get("user_pk") if isinstance(request.data, dict) else None
        try:
            user_pk = int(raw_pk or 0)
        except (TypeError, ValueError):
            return ApiResponse(code=400, detail=_("User not found"))
        target = UserInfo.objects.filter(pk=user_pk).only("is_superuser").first()
        if target is None and not UserSession.objects.filter(creator_id=user_pk).exists():
            return ApiResponse(code=400, detail=_("User not found"))
        self._ensure_target_logout_allowed(target)
        visible, _denied = self._scoped_user_pks({user_pk})
        if user_pk not in visible:
            raise PermissionDenied(_("No permission to operate this user"))
        channels = force_logout_user(user_pk, operator=request.user)
        return ApiResponse(
            data={"channels": channels},
            detail=_("User forced offline (all sessions revoked)"),
        )

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "session_pks": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
                    "user_pks": build_array_type(build_basic_type(OpenApiTypes.NUMBER) or {}),
                },
                description="session_pks=会话行主键；user_pks=会话所属用户主键。"
                "不接受未标注类型的混合数组（整数不再被猜测为用户/登录日志主键）。",
            )
        ),
        responses=get_default_response_schema(
            {
                "users": build_basic_type(OpenApiTypes.NUMBER),
                "channels": build_basic_type(OpenApiTypes.NUMBER),
                "failures": build_array_type(
                    build_object_type(
                        properties={
                            "pk": build_basic_type(OpenApiTypes.STR),
                            "reason": build_basic_type(OpenApiTypes.STR),
                        }
                    )
                    or {}
                ),
            }
        ),
    )
    @action(methods=["post"], detail=False, url_path="batch-force-logout")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def batch_force_logout(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """批量强制下线（按选中行的用户去重后踢全部会话）

        入参两种形态，禁止按主键类型猜测语义（历史实现把整数同时当用户主键与
        登录日志主键取并集，会误踢无关用户）：

        - ``{"session_pks": [...], "user_pks": [...]}``：显式区分会话行与用户；
        - ``[...]``：旧版数组形态，只接受**会话行主键（UUID）**。
        """
        data = request.data
        if isinstance(data, dict):
            raw_sessions = data.get("session_pks") or []
            raw_users = data.get("user_pks") or []
        elif isinstance(data, list):
            raw_sessions = data
            raw_users = []
        else:
            return ApiResponse(code=400, detail=_("Invalid parameter format"))
        if not isinstance(raw_sessions, (list, tuple)) or not isinstance(raw_users, (list, tuple)):
            return ApiResponse(code=400, detail=_("Invalid parameter format"))
        session_pks = [str(pk) for pk in raw_sessions if str(pk).strip()]
        user_pks: set[int] = set()
        for pk in raw_users:
            try:
                user_pks.add(int(pk))
            except (TypeError, ValueError):
                return ApiResponse(code=400, detail=_("Invalid parameter format"))
        # 两种入参形态都必须校验会话主键；对象形态直接过滤 UUIDField 会抛 500
        for pk in session_pks:
            try:
                uuid.UUID(pk)
            except ValueError:
                return ApiResponse(code=400, detail=_("Invalid parameter format"))
        # 用原始 queryset（不做在线交集过滤）：选中行来自列表快照，请求瞬间
        # 用户可能刚好掉线，被踢语义仍应生效。
        session_user_pks, denied_sessions = self._scoped_session_pks(session_pks)
        scoped_user_pks, denied_users = self._scoped_user_pks(user_pks)
        targets = (session_user_pks | scoped_user_pks) - denied_users
        # 超管目标逐用户过滤：非超管整批踢超管属于提权，过滤项回传 failures
        allowed_pks, failures = filter_force_logout_targets(request.user, targets)
        failures = [
            *failures,
            *({"pk": str(pk), "reason": str(_("No permission to operate this user"))} for pk in sorted(denied_users)),
            *({"pk": str(pk), "reason": str(_("Session not found or out of scope"))} for pk in denied_sessions),
        ]
        # 批量踢线走批量原语（缓存/黑名单/WS 逐用户循环收敛为整批一次）
        channels = force_logout_users(allowed_pks, operator=request.user)
        return ApiResponse(
            data={"users": len(allowed_pks), "channels": channels, "failures": failures},
            detail=_("Batch force offline submitted: {} users").format(len(allowed_pks)),
        )
