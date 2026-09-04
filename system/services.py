#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
system app 对外服务契约层。

其他 app 需要使用 system 的业务能力时，只允许从本模块导入，
禁止直接 import system.views / system.serializers / system.models 等内部实现，
避免 app 间横向依赖扩散。

模型再导出说明：UserInfo / UserLoginLog / UploadFile 属于跨 app 关联
（isinstance 判断、类型标注、related field queryset 等场景），
统一经由本模块引用，禁止绕过契约层直接 import system.models。
"""
from system.models import UploadFile, UserLoginLog, UserInfo
from system.serializers.userinfo import UserInfoSerializer

# login_success 经由下方 __getattr__ 惰性导出（不进 __all__，from-import 仍可用）
__all__ = [
    "UserInfo",
    "UserLoginLog",
    "UploadFile",
    "get_superusers",
    "get_active_superuser_queryset",
    "get_users_by_pks",
    "get_active_user_pk_by_username",
    "serialize_user_info",
]


def __getattr__(name):
    # login_success 位于视图层（system.views.auth.login），import 链很重
    # （login → verify_code → common.tasks），顶层导入会与
    # common.tasks → common.notifications → notifications.services 形成
    # 循环导入，因此按需加载。
    if name == "login_success":
        from system.views.auth.login import login_success

        return login_success
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_superusers():
    """超级管理员 queryset（不过滤启用状态，保持历史行为）。"""
    return UserInfo.objects.filter(is_superuser=True)


def get_active_superuser_queryset():
    """在用的超级管理员 queryset。"""
    return UserInfo.objects.filter(is_superuser=True, is_active=True)


def get_users_by_pks(pks):
    """按主键批量取用户。"""
    return UserInfo.objects.filter(id__in=pks).all()


def get_active_user_pk_by_username(username):
    """按用户名取在用用户主键，不存在返回 None。"""
    return (
        UserInfo.objects.filter(username=username, is_active=True)
        .values_list('pk', flat=True)
        .first()
    )


def serialize_user_info(user) -> dict:
    """按对外契约序列化用户信息（供 WebSocket 等非 DRF 场景使用）。"""
    return UserInfoSerializer(instance=user).data
