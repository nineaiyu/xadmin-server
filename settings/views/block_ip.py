#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : block_ip
# author : ly_13
# date : 8/12/2024
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.http import Http404
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.request import Request

from common.core.modelset import ListDeleteModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils.ip.utils import contains_ip
from settings.models import Setting
from settings.serializers.security import SecurityBlockIPSerializer
from settings.utils.ip_block import FilterIps, IpUtils
from settings.utils.security import LoginIpBlockUtil


class SecurityBlockIpViewSet(ListDeleteModelSet):
    """Ip拦截名单"""

    serializer_class = SecurityBlockIPSerializer
    queryset = Setting.objects.none()

    def filter_queryset(self, obj: Any) -> FilterIps:
        # 为啥写函数，去没有加(), 因为只有在序列化的时候，才会判断，如果是方法就执行，减少资源浪费
        data = [
            {"ip": ip, "pk": IpUtils(ip).ip_to_int, "created_time": LoginIpBlockUtil(ip).get_block_info} for ip in obj
        ]
        return FilterIps(data)

    def get_queryset(self) -> list[str]:
        ips: list[str] = []
        prefix = LoginIpBlockUtil.BLOCK_KEY_TMPL.replace("{}", "")
        # iter_keys 走 Redis SCAN 游标增量遍历；keys() 的 KEYS 命令是全库扫描，
        # 拦截键量大时会阻塞整个 Redis 实例。两者经同一套 key_func/reverse_key
        # 拼接与还原键前缀，命中范围与返回的逻辑键口径不变。
        keys = cache.iter_keys(f"{prefix}*")
        for key in keys:
            ips.append(key.replace(prefix, ""))

        white_list = [str(item) for item in settings.SECURITY_LOGIN_IP_WHITE_LIST if str(item).strip()]
        ips = [ip for ip in set(ips) if ip != "*" and not contains_ip(ip, white_list)]
        return ips

    def get_object(self) -> str:
        pk = str(self.kwargs.get("pk") or "")
        try:
            return IpUtils(pk).int_to_ip()
        except (ValueError, OSError) as exc:
            # URL 段允许任意非斜杠字符：非法整数按「不存在」处理，不把 500 暴露给扫描器
            raise Http404 from exc

    def perform_destroy(self, ip: str) -> tuple[int, int]:
        LoginIpBlockUtil(ip).clean_block_if_need()
        return 1, 1

    @extend_schema(
        request=OpenApiRequest(build_array_type(build_basic_type(OpenApiTypes.STR) or {})),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-destroy")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def batch_destroy(self, request: Request, *args: Any, **kwargs: Any) -> Any:
        """批量解除拦截

        数据源是 Redis 键列表（非 ORM queryset），逐条走 perform_destroy

        框架批量删除的非逐行分支直接调 ``queryset.delete()``——列表没有该方法，
        且解除拦截的副作用只在 perform_destroy 中；故此处自担批量语义。
        """
        if not isinstance(request.data, (list, tuple)):
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
        ips = self.filter_queryset(self.get_queryset()).filter(pk__in=request.data)
        count = 0
        for ip in ips:
            self.perform_destroy(ip)
            count += 1
        return ApiResponse(detail=_("Operation successful. Batch deleted {} data").format(count))
