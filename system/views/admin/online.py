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

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.decorators import action

from common.core.config import SysConfig
from common.core.filter import BaseFilterSet, PkMultipleFilter
from common.core.modelset import ListDeleteModelSet, OnlyExportDataAction
from common.core.pagination import DynamicPageNumber
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from message.services import get_online_info, send_logout_msg
from system.models import UserInfo, UserSession
from system.serializers.log import UserSessionSerializer
from system.utils.session import force_logout_user


class UserOnlineFilter(BaseFilterSet):
    creator_id = PkMultipleFilter(input_type="api-search-user")

    class Meta:
        model = UserSession
        fields = ["creator_id", "login_type"]


class UserOnlineViewSet(ListDeleteModelSet, OnlyExportDataAction):
    """在线用户会话（WS 连接 + 纯 HTTP 会话统一视图）"""

    queryset = UserSession.objects.all()
    serializer_class = UserSessionSerializer
    pagination_class = DynamicPageNumber(1000)
    filterset_class = UserOnlineFilter
    ordering_fields = ["last_active", "created_time"]

    def get_queryset(self):
        online_user_pks, online_user_sockets = get_online_info()
        cutoff = timezone.now() - timedelta(seconds=SysConfig.SESSION_ONLINE_TIMEOUT)
        # WS 会话看 channel 存活；HTTP 会话看活跃窗口（status=ONLINE 过滤已下线记录）
        return self.queryset.filter(status=UserSession.Status.ONLINE).filter(
            Q(channel_name__in=online_user_sockets) | Q(channel_name="", last_active__gte=cutoff)
        )

    def perform_destroy(self, instance):
        """行维度「下线」：WS 会话踢 channel；HTTP 会话按 sid 服务端失效 token。"""
        if instance.channel_name:
            send_logout_msg(instance.creator_id, [instance.channel_name])
        else:
            from common.cache.storage import SessionTokenRevokedCache

            # 精确踢本会话：登录时写入的 sid claim，refresh 续命同样失效，
            # 不影响该用户其他在用登录（与用户级 UserTokenRevokedCache 互补）
            SessionTokenRevokedCache(instance.pk).set_storage_cache(1)
        instance.mark_offline()
        return True

    @extend_schema(
        request=None,
        responses=get_default_response_schema({"channels": build_basic_type(OpenApiTypes.NUMBER)}),
    )
    @action(methods=["post"], detail=True, url_path="force-logout")
    def force_logout(self, request, *args, **kwargs):
        """强制下线该用户全部会话（服务端令牌失效 + WS 踢线）

        detail pk 为用户主键（非会话行主键）：踢的是「用户全部会话」，
        与行维度的下线（destroy 单会话）互补。
        """
        user_pk = kwargs.get("pk")
        if (
            not UserSession.objects.filter(creator_id=user_pk).exists()
            and not UserInfo.objects.filter(pk=user_pk).exists()
        ):
            return ApiResponse(code=400, detail=_("User not found"))
        channels = force_logout_user(user_pk, operator=request.user)
        return ApiResponse(
            data={"channels": channels},
            detail=_("User forced offline (all sessions revoked)"),
        )

    @extend_schema(
        request=OpenApiRequest(build_array_type(build_basic_type(OpenApiTypes.STR))),
        responses=get_default_response_schema({"users": build_basic_type(OpenApiTypes.NUMBER)}),
    )
    @action(methods=["post"], detail=False, url_path="batch-force-logout")
    def batch_force_logout(self, request, *args, **kwargs):
        """批量强制下线（按选中行的用户去重后踢全部会话）"""
        pks = [str(pk) for pk in request.data if str(pk).strip()]
        # 用原始 queryset（不做在线交集过滤）：选中行来自列表快照，请求瞬间
        # 用户可能刚好掉线，被踢语义仍应生效。
        # 兼容三种入参：会话行 pk（UUID）/ 用户 pk / 旧版登录日志行 pk（整型）。
        # UUID 解析失败的项只参与整型/用户维度查询，避免 pk 字段校验 500。
        from system.models import UserLoginLog

        session_pks = []
        for pk in pks:
            try:
                session_pks.append(str(uuid.UUID(pk)))
            except ValueError:
                continue
        user_pks = (
            set(UserSession.objects.filter(pk__in=session_pks).values_list("creator_id", flat=True))
            | set(UserSession.objects.filter(creator_id__in=pks).values_list("creator_id", flat=True))
            | set(UserLoginLog.objects.filter(pk__in=pks).values_list("creator_id", flat=True))
        )
        channels = 0
        for user_pk in user_pks:
            channels += force_logout_user(user_pk, operator=request.user)
        return ApiResponse(
            data={"users": len(user_pks), "channels": channels},
            detail=_("Batch force offline submitted: {} users").format(len(user_pks)),
        )
