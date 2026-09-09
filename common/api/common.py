#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : common
# author : ly_13
# date : 6/7/2024
import time
import uuid

from django.utils import translation
from drf_spectacular.plumbing import build_object_type, build_basic_type, build_array_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest, OpenApiResponse
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from common.cache.storage import CommonResourceIDsCache
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils.country import COUNTRY_CALLING_CODES, COUNTRY_CALLING_CODES_ZH
from common.utils.health import probe_celery, probe_db, probe_redis


class ResourcesIDCacheAPIView(GenericAPIView):
    """资源ID 缓存"""

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"resources": build_array_type(build_basic_type(OpenApiTypes.STR))},
                required=["resources"],
                description="主键列表",
            )
        ),
        responses=get_default_response_schema({"spm": build_basic_type(OpenApiTypes.STR)}),
    )
    def post(self, request, *args, **kwargs):
        """添加临时资源数据"""
        spm = str(uuid.uuid4())
        resources = request.data.get("resources")
        if resources is not None:
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
    def get(self, request, *args, **kwargs):
        """获取城市手机号列表"""
        current_lang = translation.get_language()
        if current_lang == "zh-hans":
            return ApiResponse(data=COUNTRY_CALLING_CODES_ZH)
        else:
            return ApiResponse(data=COUNTRY_CALLING_CODES)


class HealthCheckAPIView(GenericAPIView):
    """获取服务健康状态"""

    permission_classes = (AllowAny,)

    # 探测逻辑已抽至 common/utils/health.py（与监控面板共用），此处保留方法名以兼容既有调用方
    @staticmethod
    def get_db_status():
        return probe_db()

    @staticmethod
    def get_redis_status():
        return probe_redis()

    @staticmethod
    def get_celery_status():
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
                        "time": build_basic_type(OpenApiTypes.FLOAT),
                        "db_time": build_basic_type(OpenApiTypes.FLOAT),
                        "redis_time": build_basic_type(OpenApiTypes.FLOAT),
                        "celery_time": build_basic_type(OpenApiTypes.FLOAT),
                    }
                )
            )
        }
    )
    def get(self, request):
        """获取服务健康状态"""
        redis_status, redis_time = self.get_redis_status()
        db_status, db_time = self.get_db_status()
        celery_status, celery_time = self.get_celery_status()
        # status 只反映核心依赖（DB/Redis）；worker 离线不判定服务不健康（导入导出降级可用）
        status = all([redis_status, db_status])
        data = {
            "status": status,
            "db_status": db_status,
            "redis_status": redis_status,
            "celery_status": celery_status,
            "time": int(time.time()),
            "db_time": db_time,
            "redis_time": redis_time,
            "celery_time": celery_time,
        }
        return Response(data)
