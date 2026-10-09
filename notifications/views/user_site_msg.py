#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : user_site_msg
# author : ly_13
# date : 9/15/2024
from typing import Any

from django.core.cache import cache
from django.db.models import Q, QuerySet
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from common.core.filter import BaseFilterSet
from common.core.modelset import CacheListResponseMixin, OnlyListModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from notifications.models import MessageContent, MessageUserRead
from notifications.serializers.message import UserNoticeSerializer

logger = get_logger(__name__)

# 未读汇总短缓存：list / unread 是前端角标轮询热点，每次请求一次 OR-join count；
# 30s 短缓存 + 标记已读主动失效（新发布最多滞后 TTL，站内信角标为最终一致展示）
UNREAD_SUMMARY_CACHE_TTL = 30
UNREAD_SUMMARY_CACHE_PREFIX = "notify_unread_summary_"
# 不影响未读总数的请求参数（分页/排序/元信息）：除此之外的筛选按实时口径统计
UNREAD_IRRELEVANT_PARAMS = frozenset({"page", "size", "ordering", "with_meta", "type"})


def unread_summary_cache_key(user_pk: Any) -> str:
    return f"{UNREAD_SUMMARY_CACHE_PREFIX}{user_pk}"


def get_unread_summary(user: Any) -> dict[str, Any]:
    """未读汇总（角标口径）：公告类 / 私信类 / 合计（30s 短缓存）。"""
    key = unread_summary_cache_key(getattr(user, "pk", "anonymous"))
    try:
        cached = cache.get(key)
    except Exception:  # noqa: BLE001 缓存不可用直接查库
        cached = None
    if isinstance(cached, dict):
        return cached
    base = MessageContent.objects.filter(publish=True)
    notice = base.filter(get_user_unread_q2(user)).count()
    announce = base.filter(get_user_unread_q1(user)).count()
    data = {"notice": notice, "announce": announce, "total": notice + announce}
    try:
        cache.set(key, data, UNREAD_SUMMARY_CACHE_TTL)
    except Exception:  # noqa: BLE001
        logger.debug("cache unread summary failed", exc_info=True)
    return data


def invalidate_unread_summary(user: Any) -> None:
    """标记已读后失效缓存（角标立即归零）。"""
    try:
        cache.delete(unread_summary_cache_key(getattr(user, "pk", "anonymous")))
    except Exception:  # noqa: BLE001
        logger.debug("invalidate unread summary failed", exc_info=True)


def get_users_notice_q(user_obj: Any) -> Any:
    q = Q()
    q |= Q(notice_type=MessageContent.NoticeChoices.NOTICE)
    q |= Q(notice_type=MessageContent.NoticeChoices.DEPT, notice_dept=user_obj.dept)
    q |= Q(notice_type=MessageContent.NoticeChoices.ROLE, notice_role__in=user_obj.roles.all())
    # 岗位通知：仅启用岗位的在岗用户可见（与审批人解析/计数同口径）
    q |= Q(
        notice_type=MessageContent.NoticeChoices.POST,
        notice_post__in=user_obj.posts.filter(is_active=True, deleted_at__isnull=True),
    )
    return q


def get_user_unread_q1(user_obj: Any) -> Any:
    return get_users_notice_q(user_obj) & ~Q(notice_user=user_obj)


def get_user_unread_q2(user_obj: Any) -> Any:
    return Q(notice_type__in=MessageContent.get_user_choices(), notice_user=user_obj, messageuserread__unread=True)


def get_user_unread_q(user_obj: Any) -> Any:
    return get_user_unread_q1(user_obj) | get_user_unread_q2(user_obj)


class UserSiteMessageViewSetFilter(BaseFilterSet):
    message = filters.CharFilter(field_name="message", lookup_expr="icontains")
    title = filters.CharFilter(field_name="title", lookup_expr="icontains")
    unread = filters.BooleanFilter(field_name="unread", method="unread_filter")

    def unread_filter(self, queryset: Any, name: Any, value: Any) -> Any:
        if value:
            return queryset.filter(get_user_unread_q(self.request.user))
        else:
            return queryset.filter(notice_user=self.request.user, messageuserread__unread=False)

    class Meta:
        model = MessageContent
        fields = ["title", "message", "pk", "notice_type", "unread", "level"]


class UserSiteMessageViewSet(OnlyListModelSet, CacheListResponseMixin):
    """用户消息中心"""

    queryset = MessageContent.objects.filter(publish=True).all().distinct()
    serializer_class = UserNoticeSerializer
    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ["created_time"]
    filterset_class = UserSiteMessageViewSetFilter

    def list(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        if set(request.query_params) - UNREAD_IRRELEVANT_PARAMS:
            # 带筛选条件：未读数按当前条件实时统计（与原语义一致）
            unread_count = (
                self.filter_queryset(self.get_queryset()).filter(get_user_unread_q(self.request.user)).count()
            )
        else:
            # 角标口径：无筛选时走 30s 短缓存（每次轮询一次 OR-join count 的热点）
            unread_count = get_unread_summary(request.user)["total"]
        q = get_users_notice_q(request.user)
        q |= Q(notice_type__in=MessageContent.get_user_choices(), notice_user=request.user)
        self.queryset = self.filter_queryset(self.get_queryset()).filter(q)
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, unread_count=unread_count)

    @extend_schema(
        parameters=[],
        responses={
            200: inline_serializer(
                name="unread",
                fields={
                    "code": serializers.IntegerField(),
                    "detail": serializers.CharField(),
                    "data": inline_serializer(
                        name="data",
                        fields={
                            "results": inline_serializer(
                                name="results",
                                fields={
                                    "key": serializers.CharField(),
                                    "name": serializers.CharField(),
                                    "list": UserNoticeSerializer(many=True),
                                    "total": serializers.IntegerField(),
                                },
                            ),
                            "total": serializers.IntegerField(),
                        },
                    ),
                },
            )
        },
    )
    @action(methods=["get"], detail=False)  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def unread(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """用户未读消息"""
        summary = get_unread_summary(request.user)
        has_filters = bool(set(request.query_params) - UNREAD_IRRELEVANT_PARAMS)
        notice_queryset = self.filter_queryset(self.get_queryset()).filter(get_user_unread_q2(request.user))
        announce_queryset = self.filter_queryset(self.get_queryset()).filter(get_user_unread_q1(request.user))
        results = [
            {
                "key": "1",
                "name": "layout.notice",
                # 传 list 使 serializer.instance 为列表，get_page_instances 才能拿到整页做批量查询
                "list": self.serializer_class(list(notice_queryset[:10]), many=True, context={"request": request}).data,
                # 角标口径：无筛选时用短缓存合计（与 list 的未读总数同源），带筛选按条件实时统计
                "total": notice_queryset.count() if has_filters else summary["notice"],
            },
            {
                "key": "2",
                "name": "layout.announcement",
                "list": self.serializer_class(
                    list(announce_queryset[:10]), many=True, context={"request": request}
                ).data,
                "total": announce_queryset.count() if has_filters else summary["announce"],
            },
        ]

        return ApiResponse(data={"results": results, "total": sum([item.get("total", 0) for item in results])})

    def read_message(self, pks: Any, request: Any) -> Any:
        """批量已读：固定 3 条 SQL，与 pks 数量无关（旧实现为 2N 条）。

        两种入参形态：
        - 显式 pk 列表（batch-read）：应用侧去重后按 pk 过滤，行为与历史口径一致；
        - pk queryset（all-read）：pk 过滤与「尚无该用户已读行」差集全部下推 DB——
          公告全量下发时单用户未读 pk 可达数千，不再整表物化成 list，仅待补建行
          迭代后分批 bulk_create。
        """
        if isinstance(pks, QuerySet):
            # 1. 已存在的未读记录批量置为已读（pk 集合以子查询下推）
            MessageUserRead.objects.filter(notice__in=pks, owner=request.user, unread=True).update(unread=False)
            # 2+3. 仅对尚无该用户已读行的消息（DB 反连接差集）补建"已读"行
            missing_pks = (
                MessageContent.objects.filter(pk__in=pks)
                .exclude(messageuserread__owner=request.user)
                .values_list("pk", flat=True)
            )
        else:
            pks = list(set(pks))
            if not pks:
                return ApiResponse()
            # 1. 已存在的未读记录批量置为已读
            MessageUserRead.objects.filter(notice__id__in=pks, owner=request.user, unread=True).update(unread=False)
            # 2. 已存在的记录（无论原状态）不再重复创建
            exist_ids = set(
                MessageUserRead.objects.filter(notice__id__in=pks, owner=request.user).values_list(
                    "notice_id", flat=True
                )
            )
            # 3. 仅对尚无记录的消息补建"已读"行
            missing_pks = [pk for pk in pks if pk not in exist_ids]
        new_reads = [MessageUserRead(owner=request.user, notice_id=pk, unread=False) for pk in missing_pks]
        # 未读量大（如系统公告全量下发）时一次性 bulk_create 会生成超大 INSERT：分批写入
        for start in range(0, len(new_reads), 1000):
            MessageUserRead.objects.bulk_create(new_reads[start : start + 1000])
        # 已读后角标立即归零（未读汇总短缓存失效）
        invalidate_unread_summary(request.user)
        return ApiResponse()

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"pks": build_array_type(build_basic_type(OpenApiTypes.STR) or {})},
                required=["pks"],
                description="主键列表",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["patch"], detail=False, url_path="batch-read")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def batch_read(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """批量已读消息"""
        pks = request.data.get("pks", [])
        return self.read_message(pks, request)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["patch"], detail=False, url_path="all-read")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def all_read(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """全部已读消息"""
        # 未读 pk 集合保持 queryset 形态传给 read_message：pk 去重/差集全部下推
        # DB（公告全量下发时不把数千 pk 物化成内存 list）
        queryset = self.filter_queryset(self.get_queryset()).filter(get_user_unread_q(self.request.user))
        return self.read_message(queryset.values_list("pk", flat=True).distinct(), request)
