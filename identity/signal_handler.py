#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""identity 域信号接收器：用户 / 角色 / 部门 / 授权缓存失效 + 内置角色同步。

自 system/signal_handler.py 按模型域拆出；Menu/MenuMeta/SystemConfig/Tag/
DataPermission/DataDict 等平台面接收器留在 system 侧同名模块。
"""

import itertools

from django.contrib.auth import user_logged_out
from django.db.models.signals import m2m_changed, post_migrate, post_save, pre_delete
from django.dispatch import receiver

from common.base.magic import MagicCacheData, cache_response
from common.core.filter import invalidate_data_permission_grants_cache
from common.utils import get_logger
from identity.models import DeptInfo, UserInfo, UserRole
from identity.signal import invalid_user_cache_signal

logger = get_logger(__name__)

# m2m 直改（绕过 API 不触发实例 save）同样需要失效权限缓存
M2M_CHANGED_ACTIONS = ("post_add", "post_remove", "post_clear")


def get_cache_data_keys(pks):
    for pk in pks:
        for method in ["GET", "PUT", "DELETE", "POST", "PATCH"]:
            yield f"get_user_permission_{pk}_{method}"


def get_cache_response_keys(pks):
    for pk in pks:
        yield f"UserRoutesAPIView_get_{pk}"


def batch_invalid_cache(pks, batch_length=1000):
    cleans = [
        (MagicCacheData.invalid_caches, get_cache_data_keys(pks)),
        (cache_response.invalid_caches, get_cache_response_keys(pks)),
    ]
    for keys in cleans:
        for data in itertools.batched(keys[1], batch_length, strict=False):
            keys[0](data)


def invalidate_menu_user_caches(menus) -> None:
    """失效菜单相关的用户权限/路由缓存（Menu 与 MenuMeta 变更共用同一实现）。

    失效面 = 全部超管（auths 快照含全部启用权限点）+ 拥有这些菜单的角色所属用户
    + 通过角色间接持有的部门用户；另清应用授权的 path→pk 映射短缓存。
    接受菜单实例序列（调用方可能一次变更多个：批量生成权限点、父菜单 + 子权限点）。
    """
    menus = [menu for menu in menus if menu is not None]
    if not menus:
        return
    from identity.utils.api_grant import invalid_menu_path_cache

    batch_invalid_cache(UserInfo.objects.filter(is_superuser=True).values_list("pk", flat=True))
    pk1 = UserRole.objects.filter(menu__in=menus, userinfo__isnull=False).values_list("userinfo", flat=True).distinct()
    pk2 = DeptInfo.objects.filter(roles__menu__in=menus).values_list("dept_query", flat=True).distinct()
    batch_invalid_cache(set(pk1) | set(pk2))
    # 应用授权的 path→pk 映射短缓存（超管/白名单出口按地址回查菜单）同源失效
    invalid_menu_path_cache()


@receiver([post_save, pre_delete], sender=UserRole)
def invalid_role_cache_handler(sender, instance, **kwargs):
    pk1 = instance.userinfo_set.values_list("pk", flat=True).distinct()
    pk2 = DeptInfo.objects.filter(roles=instance).values_list("dept_query", flat=True).distinct()
    batch_invalid_cache(set(pk1) | set(pk2))
    logger.info(f"invalid cache {instance}")


@receiver([post_save, pre_delete], sender=DeptInfo)
def invalid_dept_cache_handler(sender, instance, **kwargs):
    batch_invalid_cache(instance.userinfo_set.values_list("pk", flat=True).distinct())
    # 部门树变化会影响下级/上级递归结果，缓存一并失效
    DeptInfo.invalid_dept_tree_cache()
    # 部门启用状态 / 层级变化会改变授权池的部门链，授权池缓存一并失效
    invalidate_data_permission_grants_cache()
    logger.info(f"invalid cache {instance}")


@receiver(m2m_changed, sender=UserInfo.rules.through)
def invalid_user_rules_m2m_cache_handler(sender, instance, action, **kwargs):
    # 用户-授权关系直改：该用户授权池立即失效（走全局版本号）
    if action not in M2M_CHANGED_ACTIONS:
        return
    invalidate_data_permission_grants_cache()
    logger.info(f"invalid data permission grants cache by user rules m2m {instance}")


@receiver(m2m_changed, sender=UserRole.menu.through)
def invalid_role_menu_m2m_cache_handler(sender, instance, action, **kwargs):
    if action not in M2M_CHANGED_ACTIONS:
        return
    invalid_role_cache_handler(sender=UserRole, instance=instance)


@receiver([post_save, pre_delete], sender=UserInfo)
def invalid_user_cache_handler(sender, instance, **kwargs):
    batch_invalid_cache([instance.pk])
    logger.info(f"invalid cache {instance}")


# 清理用户相关缓存，用户登出会自动清理
@receiver([invalid_user_cache_signal, user_logged_out])
def invalid_user_cache(sender, **kwargs):
    user_pk = kwargs.get("user_pk", None)
    user = kwargs.get("user", None)
    if isinstance(user, UserInfo):
        user_pk = user.pk
    if user_pk is None:
        return

    batch_invalid_cache([user_pk])


@receiver(m2m_changed, sender=DeptInfo.rules.through)
def invalid_dept_rules_m2m_cache_handler(sender, instance, action, **kwargs):
    # 部门-授权关系直改：部门链下的授权池立即失效（走全局版本号）
    if action not in M2M_CHANGED_ACTIONS:
        return
    invalidate_data_permission_grants_cache()
    logger.info(f"invalid data permission grants cache by dept rules m2m {instance}")


@receiver(m2m_changed, sender=UserInfo.roles.through)
def invalid_user_roles_m2m_cache_handler(sender, instance, action, **kwargs):
    if action not in M2M_CHANGED_ACTIONS:
        return
    batch_invalid_cache([instance.pk])


@receiver(m2m_changed, sender=DeptInfo.roles.through)
def invalid_dept_roles_m2m_cache_handler(sender, instance, action, **kwargs):
    if action not in M2M_CHANGED_ACTIONS:
        return
    batch_invalid_cache(instance.userinfo_set.values_list("pk", flat=True).distinct())


@receiver(post_migrate, dispatch_uid="identity.signal_handler.sync_builtin_roles")
def post_migrate_sync_builtin_roles(sender, **kwargs):
    """migrate 后同步内置角色（幂等）：
    全新库 migrate 完成即有可用角色，存量库升级同样生效；同步失败不阻断 migrate。"""
    if getattr(sender, "name", None) != "identity":
        return
    from identity.builtin import sync_builtin_roles

    try:
        changed = sync_builtin_roles()
        logger.info("builtin roles synced via post_migrate, changed: %s", changed)
    except Exception:
        logger.exception("sync builtin roles failed")
