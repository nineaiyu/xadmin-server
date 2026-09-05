#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
system app 对外服务契约层。

其他 app 需要使用 system 的业务能力时，只允许从本模块导入，
禁止直接 import system.views / system.serializers / system.models 等内部实现，
避免 app 间横向依赖扩散。

模型再导出说明：跨 app 关联（isinstance 判断、类型标注、related field
queryset、serializer Meta.model 等场景）统一经由本模块引用，禁止绕过
契约层直接 import system.models。

惰性导出说明：模型 / 序列化器 / 信号经 __getattr__ 按需加载并缓存到模块
globals，``from system.services import Menu`` 这类 from-import 仍然可用；
本模块自身保持零重导入（顶层不 import system.models 等），供 common.core
等底层模块安全顶层引用，避免循环导入。login_success 位于视图层
（import 链很重：login → verify_code → common.tasks），同理按需加载。
"""

__all__ = [
    # 模型契约
    "UserInfo",
    "UserLoginLog",
    "UploadFile",
    "SystemConfig",
    "UserPersonalConfig",
    "OperationLog",
    "Menu",
    "FieldPermission",
    "DataPermission",
    "ModeTypeAbstract",
    "DeptInfo",
    "ModelLabelField",
    # 序列化器契约
    "UserInfoSerializer",
    # 信号契约
    "invalid_user_cache_signal",
    # 服务函数
    "get_superusers",
    "get_active_superuser_queryset",
    "get_users_by_pks",
    "get_active_user_pk_by_username",
    "serialize_user_info",
]

# 惰性再导出表：名字 -> 所属模块
_LAZY_EXPORTS = {
    "UserInfo": "system.models",
    "UserLoginLog": "system.models",
    "UploadFile": "system.models",
    "SystemConfig": "system.models",
    "UserPersonalConfig": "system.models",
    "OperationLog": "system.models",
    "Menu": "system.models",
    "FieldPermission": "system.models",
    "DataPermission": "system.models",
    "ModeTypeAbstract": "system.models",
    "DeptInfo": "system.models",
    "ModelLabelField": "system.models",
    "UserInfoSerializer": "system.serializers.userinfo",
    "invalid_user_cache_signal": "system.signal",
}


def __getattr__(name):
    if name == "login_success":
        from system.views.auth.login import login_success

        globals()[name] = login_success
        return login_success

    module_path = _LAZY_EXPORTS.get(name)
    if module_path is not None:
        from importlib import import_module

        value = getattr(import_module(module_path), name)
        globals()[name] = value  # 首次访问后缓存为模块属性
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_superusers():
    """超级管理员 queryset（不过滤启用状态，保持历史行为）。"""
    from system.models import UserInfo

    return UserInfo.objects.filter(is_superuser=True)


def get_active_superuser_queryset():
    """在用的超级管理员 queryset。"""
    from system.models import UserInfo

    return UserInfo.objects.filter(is_superuser=True, is_active=True)


def get_users_by_pks(pks):
    """按主键批量取用户。"""
    from system.models import UserInfo

    return UserInfo.objects.filter(id__in=pks).all()


def get_active_user_pk_by_username(username):
    """按用户名取在用用户主键，不存在返回 None。"""
    from system.models import UserInfo

    return UserInfo.objects.filter(username=username, is_active=True).values_list("pk", flat=True).first()


def serialize_user_info(user) -> dict:
    """按对外契约序列化用户信息（供 WebSocket 等非 DRF 场景使用）。"""
    from system.serializers.userinfo import UserInfoSerializer

    return UserInfoSerializer(instance=user).data
