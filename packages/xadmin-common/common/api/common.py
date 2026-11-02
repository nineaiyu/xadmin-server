#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : common
# author : ly_13
# date : 6/7/2024
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any

from django.utils import translation
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, OpenApiResponse, extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from common.cache.storage import CommonResourceIDsCache
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils.country import COUNTRY_CALLING_CODES, COUNTRY_CALLING_CODES_ZH
from common.utils.health import probe_all, probe_celery, probe_db, probe_redis

#: 临时资源缓存单次上限：防止认证用户用大数组低成本放大 Redis 内存
RESOURCE_CACHE_MAX_ITEMS = 1000
RESOURCE_CACHE_MAX_BYTES = 64 * 1024


class ResourcesCacheThrottle(UserRateThrottle):
    """资源缓存写入的用户级节流（默认链只有匿名/PAT，登录态裸请求不受限）。"""

    scope = "resources_cache"
    rate = "120/m"


class ResourcesIDCacheAPIView(GenericAPIView):
    """资源ID 缓存"""

    permission_classes = (IsAuthenticated,)
    throttle_classes = (ResourcesCacheThrottle,)

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"resources": build_array_type(build_basic_type(OpenApiTypes.STR) or {})},
                required=["resources"],
                description="主键列表",
            )
        ),
        responses=get_default_response_schema({"spm": build_basic_type(OpenApiTypes.STR)}),
    )
    def post(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """添加临时资源数据"""
        spm = str(uuid.uuid4())
        resources = request.data.get("resources")
        if resources is not None:
            if not isinstance(resources, list) or len(resources) > RESOURCE_CACHE_MAX_ITEMS:
                return ApiResponse(code=1004, detail="Invalid resources payload")
            if len(str(resources)) > RESOURCE_CACHE_MAX_BYTES:
                return ApiResponse(code=1004, detail="Resources payload is too large")
            CommonResourceIDsCache(spm).set_storage_cache(resources, 300)
        return ApiResponse(spm=spm)


class CountryListAPIView(GenericAPIView):
    """城市列表"""

    permission_classes = (AllowAny,)

    @extend_schema(
        responses=get_default_response_schema(
            {
                "data": build_array_type(
                    build_object_type(
                        properties={
                            "name": build_basic_type(OpenApiTypes.STR),
                            "phone_code": build_basic_type(OpenApiTypes.STR),
                            "flag": build_basic_type(OpenApiTypes.STR),
                            "code": build_basic_type(OpenApiTypes.STR),
                        }
                    )
                )
            }
        )
    )
    def get(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取城市手机号列表"""
        current_lang = translation.get_language()
        if current_lang == "zh-hans":
            return ApiResponse(data=COUNTRY_CALLING_CODES_ZH)
        else:
            return ApiResponse(data=COUNTRY_CALLING_CODES)


#: 进程内健康端点限流（不依赖 Redis：Redis 故障时仍需判活）；每 IP 每分钟上限
_HEALTH_RATE_LIMIT = 600
_HEALTH_RATE_WINDOW = 60
_health_hits: dict[str, deque[float]] = defaultdict(deque)
_health_hits_lock = threading.Lock()


def _health_rate_limited(ident: str) -> bool:
    now = time.monotonic()
    with _health_hits_lock:
        if len(_health_hits) > 10000:  # 防异常来源无限扩键
            _health_hits.clear()
        hits = _health_hits[ident]
        while hits and now - hits[0] > _HEALTH_RATE_WINDOW:
            hits.popleft()
        if len(hits) >= _HEALTH_RATE_LIMIT:
            return True
        hits.append(now)
        return False


class HealthCheckAPIView(GenericAPIView):
    """获取服务健康状态"""

    permission_classes = (AllowAny,)
    # 基础设施端点：跳过 DRF 限流——限流计数走 redis，Redis 故障时会拖慢判活
    # （2029-10 故障演练复测：冻结时限流读 redis 增加 0.2s+ 串行等待）
    throttle_classes = ()

    # 探测逻辑已抽至 common/utils/health.py（与监控面板共用），此处保留方法名以兼容既有调用方
    @staticmethod
    def get_db_status() -> Any:
        return probe_db()

    @staticmethod
    def get_redis_status() -> Any:
        return probe_redis()

    @staticmethod
    def get_celery_status() -> Any:
        return probe_celery()

    @extend_schema(
        responses={
            200: OpenApiResponse(
                build_object_type(
                    properties={
                        "status": build_basic_type(OpenApiTypes.BOOL),
                        "db_status": build_basic_type(OpenApiTypes.BOOL),
                        "redis_status": build_basic_type(OpenApiTypes.BOOL),
                        "celery_status": build_basic_type(OpenApiTypes.BOOL),
                        "storage_status": build_basic_type(OpenApiTypes.BOOL),
                        "time": build_basic_type(OpenApiTypes.FLOAT),
                        "db_time": build_basic_type(OpenApiTypes.FLOAT),
                        "redis_time": build_basic_type(OpenApiTypes.FLOAT),
                        "celery_time": build_basic_type(OpenApiTypes.FLOAT),
                        "storage_time": build_basic_type(OpenApiTypes.FLOAT),
                        "db_error": build_basic_type(OpenApiTypes.STR),
                        "redis_error": build_basic_type(OpenApiTypes.STR),
                        "celery_error": build_basic_type(OpenApiTypes.STR),
                        "storage_error": build_basic_type(OpenApiTypes.STR),
                    }
                )
            )
        }
    )
    def get(self, request: Any) -> Any:
        """获取服务健康状态（并行探测 + 总超时预算，避免故障依赖拖挂 healthcheck）"""
        from common.utils.request import get_request_ip

        if _health_rate_limited(str(get_request_ip(request) or "unknown")):
            return Response({"detail": "Too many health check requests"}, status=429)
        results = probe_all()
        db_status, db_time = results["db"]
        redis_status, redis_time = results["redis"]
        celery_status, celery_time = results["celery"]
        storage_status, storage_time = results["storage"]
        # status 只反映核心依赖（DB/Redis）；worker 离线不判定服务不健康（导入导出降级可用）；
        # 存储后端同为可观测项：对象存储抖动不应让容器被判不健康
        status = all([redis_status, db_status])
        # 故障分支的 probe 第二项是可读原因（字符串）：契约里 *_time 只放数值，
        # 原因改由 *_error 承载，前端/监控按 number 解析不会拿到 NaN
        errors = {}
        timings = {}
        for name, value in (
            ("db", db_time),
            ("redis", redis_time),
            ("celery", celery_time),
            ("storage", storage_time),
        ):
            numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
            timings[f"{name}_time"] = float(value) if numeric else None
            errors[f"{name}_error"] = None if numeric else str(value or "unknown error")
        data = {
            "status": status,
            "db_status": db_status,
            "redis_status": redis_status,
            "celery_status": celery_status,
            "storage_status": storage_status,
            "time": int(time.time()),
            **timings,
            **{key: value for key, value in errors.items() if value is not None},
        }
        # 依赖不可用时必须返回 5xx：容器 healthcheck（curl -fsL）只看 HTTP 状态码，
        # 恒 200 会让 DB/Redis 全挂的实例继续被判 healthy、继续接流量
        return Response(data, status=200 if status else 503)
