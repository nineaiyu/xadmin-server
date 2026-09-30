#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : signal_handler.py
# author : ly_13
# date : 12/15/2023
import itertools

from django.contrib.auth import user_logged_out
from django.db.models.signals import m2m_changed, post_delete, post_migrate, post_save, pre_delete
from django.dispatch import receiver

from common.base.magic import MagicCacheData, cache_response
from common.base.utils import remove_file
from common.cache.storage import UserSystemConfigCache
from common.celery.utils import get_celery_task_log_path
from common.core.config import SysConfig
from common.core.filter import invalidate_data_permission_grants_cache
from common.utils import get_logger
from system.models import (
    DataDict,
    DataMaskRule,
    DataPermission,
    DeptInfo,
    Menu,
    MenuMeta,
    SystemConfig,
    TaskExecution,
    UserInfo,
    UserPersonalConfig,
    UserRole,
)
from system.signal import approval_instance_finished, invalid_user_cache_signal
from system.utils.api_grant import invalid_menu_path_cache
from system.utils.dict import invalid_dict_cache
from system.utils.mask import invalid_mask_cache

logger = get_logger(__name__)


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
    batch_invalid_cache(UserInfo.objects.filter(is_superuser=True).values_list("pk", flat=True))
    pk1 = UserRole.objects.filter(menu__in=menus, userinfo__isnull=False).values_list("userinfo", flat=True).distinct()
    pk2 = DeptInfo.objects.filter(roles__menu__in=menus).values_list("dept_query", flat=True).distinct()
    batch_invalid_cache(set(pk1) | set(pk2))
    # 应用授权的 path→pk 映射短缓存（超管/白名单出口按地址回查菜单）同源失效
    invalid_menu_path_cache()


@receiver([post_save, pre_delete], sender=Menu)
def clean_cache_handler(sender, instance, **kwargs):
    invalidate_menu_user_caches([instance])
    logger.info(f"invalid cache {instance}")


@receiver([post_save, pre_delete], sender=MenuMeta)
def clean_menu_meta_cache_handler(sender, instance, **kwargs):
    """菜单元数据（标题/图标/隐藏/tag/水印等）变更：路由快照同源失效。

    RouteSerializer 输出嵌套 meta；meta 独立保存（菜单页改标题/图标，或
    MenuMeta 走 ORM 直改）不触发 Menu 的 post_save——漏挂会让路由缓存最长
    24h（TTL）不更新。meta 与 Menu 是 OneToOne，反查可能不存在（先建 meta
    后绑定，或级联删除场景），按缺失跳过。
    """
    try:
        menu = instance.menu
    except Exception:  # noqa: BLE001 Meta 未绑定菜单 / 菜单已删除
        menu = None
    if menu is not None:
        invalidate_menu_user_caches([menu])
        logger.info(f"invalid menu meta cache {instance}")


@receiver([post_save, pre_delete], sender=SystemConfig)
def invalid_config_cache_handler(sender, instance, **kwargs):
    SysConfig.invalid_config_cache(instance.key)
    logger.info(f"invalid cache {instance}")


@receiver([post_save, post_delete], sender=UserPersonalConfig)
def invalid_user_config_cache_handler(sender, instance, **kwargs):
    """用户个人配置行变更（管理页/导入/ORM 直改）即时失效该用户的对应缓存键。

    管理页写个人配置不走 UserConfig.set_value，缺这条时该用户最长 30 天读不到
    管理员设置的个人值；按 owner+key 精确清理，无 wildcard 扫描开销。
    """
    UserSystemConfigCache(f"user_{instance.owner_id}_{instance.key}").del_storage_cache()
    logger.info(f"invalid user config cache {instance}")


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


@receiver([post_save, pre_delete], sender=DataPermission)
def invalid_data_permission_cache_handler(sender, instance, **kwargs):
    # 授权规则 / 模式 / 启用状态变化：授权池缓存立即失效（全局版本号自增）
    invalidate_data_permission_grants_cache()
    logger.info(f"invalid data permission grants cache {instance}")


@receiver(m2m_changed, sender=DataPermission.menu.through)
def invalid_data_permission_menu_m2m_cache_handler(sender, instance, action, **kwargs):
    # 授权-菜单绑定直改（绕过 save）同样失效授权池缓存
    if action not in M2M_CHANGED_ACTIONS:
        return
    invalidate_data_permission_grants_cache()
    logger.info(f"invalid data permission grants cache by menu m2m {instance}")


@receiver(m2m_changed, sender=UserInfo.rules.through)
def invalid_user_rules_m2m_cache_handler(sender, instance, action, **kwargs):
    # 用户-授权关系直改：该用户授权池立即失效（走全局版本号）
    if action not in M2M_CHANGED_ACTIONS:
        return
    invalidate_data_permission_grants_cache()
    logger.info(f"invalid data permission grants cache by user rules m2m {instance}")


@receiver(m2m_changed, sender=DeptInfo.rules.through)
def invalid_dept_rules_m2m_cache_handler(sender, instance, action, **kwargs):
    # 部门-授权关系直改：部门链下的授权池立即失效（走全局版本号）
    if action not in M2M_CHANGED_ACTIONS:
        return
    invalidate_data_permission_grants_cache()
    logger.info(f"invalid data permission grants cache by dept rules m2m {instance}")


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


# m2m 直改（绕过 API 不触发实例 save）同样需要失效权限缓存
M2M_CHANGED_ACTIONS = ("post_add", "post_remove", "post_clear")


@receiver(m2m_changed, sender=UserRole.menu.through)
def invalid_role_menu_m2m_cache_handler(sender, instance, action, **kwargs):
    if action not in M2M_CHANGED_ACTIONS:
        return
    invalid_role_cache_handler(sender=UserRole, instance=instance)


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


@receiver(pre_delete, sender=TaskExecution)
def delete_task_execution_log_handler(sender, **kwargs):
    # 执行历史删除（含批量删除）时联动清理落盘日志文件
    instance = kwargs.get("instance")
    if instance:
        remove_file(get_celery_task_log_path(str(instance.pk)))


@receiver([post_save, pre_delete], sender=DataDict)
def invalid_dict_cache_handler(sender, instance, **kwargs):
    # 字典类型变更失效该 code 缓存；类型删除需失效其下字典项缓存，全量失效更稳
    if instance.parent_id:
        invalid_dict_cache(instance.parent.code)
    else:
        invalid_dict_cache()
    logger.info(f"invalid dict cache {instance}")


@receiver([post_save, pre_delete], sender=DataMaskRule)
def invalid_mask_cache_handler(sender, instance, **kwargs):
    # 脱敏规则变更（含改 model/field/is_active）失效对应模型缓存
    invalid_mask_cache(instance.model if instance.model else None)
    logger.info(f"invalid mask cache {instance}")


@receiver(m2m_changed, sender=DataMaskRule.roles.through)
def invalid_mask_roles_m2m_cache_handler(sender, instance, action, **kwargs):
    # roles 直改（绕过 save）同样失效该规则所属模型缓存
    if action not in M2M_CHANGED_ACTIONS:
        return
    invalid_mask_cache(instance.model if instance.model else None)
    logger.info(f"invalid mask cache by roles m2m {instance}")


@receiver(approval_instance_finished)
def sync_business_status_handler(sender, instance, status=None, reason="", **kwargs):
    """流程实例终态回写业务单：按 biz_type 分发给业务同步器。

    目前接入请假业务（biz_type=leave）与动态表单提交（biz_type=dform_submission）；
    新增业务在此处追加分支即可（引擎侧无需改动）。回写失败只记日志——业务状态由
    审批结果驱动，不应反过来阻断审批。
    """
    biz_type = getattr(instance, "biz_type", "")
    if not biz_type:
        return
    try:
        if biz_type == "leave":
            from approval.utils.leave import sync_leave_instance

            sync_leave_instance(instance, status, reason)
        elif biz_type == "dform_submission":
            from dataset.utils.dform_flow import sync_dform_instance

            sync_dform_instance(instance, status, reason)
        elif biz_type == "demo_book":
            # demo 示例 app 的上架审批回写（demo 未装载时不会产生该 biz_type 的实例）
            from demo.services import sync_book_instance

            sync_book_instance(instance, status, reason)
        else:
            logger.warning("no business sync handler for biz_type:%s", biz_type)
    except Exception:
        logger.exception(
            "sync business status failed. instance:%s biz_type:%s", getattr(instance, "pk", None), biz_type
        )


@receiver(post_migrate, dispatch_uid="system.signal_handler.sync_builtin_roles")
def post_migrate_sync_builtin_roles(sender, **kwargs):
    """migrate 后同步内置角色（幂等）：
    全新库 migrate 完成即有可用角色，存量库升级同样生效；同步失败不阻断 migrate。"""
    if getattr(sender, "name", None) != "system":
        return
    from system.builtin import sync_builtin_roles

    try:
        changed = sync_builtin_roles()
        logger.info("builtin roles synced via post_migrate, changed: %s", changed)
    except Exception:
        logger.exception("sync builtin roles failed")


@receiver([post_save, post_delete], sender="system.Tag", dispatch_uid="system.signal_handler.clean_tag_metadata_cache")
def clean_tag_metadata_cache_handler(sender, instance, **kwargs):
    """标签变更：下拉选项缓存 + 元数据载荷缓存同源失效。

    标签名单在构建 user 等列表元数据时被固化进载荷（TagChoiceFilter choices），
    元数据载荷缓存后「新建标签立即可选」需要随变更失效，否则最长 5min（TTL）
    不可选——并行 e2e 实测踩中（预热缓存 + 新建标签 → 下拉缺项）。ORM 直改
    （内置标签同步）也走此信号，故挂模型而非视图。
    """
    from common.core.modelset.metadata import invalidate_metadata_payload_cache
    from system.utils.tags import invalidate_tag_options_cache

    invalidate_tag_options_cache()
    invalidate_metadata_payload_cache()
    logger.info(f"invalid tag derived caches {instance}")


@receiver(post_migrate, dispatch_uid="system.signal_handler.sync_builtin_tags")
def post_migrate_sync_builtin_tags(sender, **kwargs):
    """migrate 后同步内置标签（幂等）：与内置角色同一时点与容错口径。"""
    if getattr(sender, "name", None) != "system":
        return
    from system.builtin import sync_builtin_tags

    try:
        changed = sync_builtin_tags()
        logger.info("builtin tags synced via post_migrate, changed: %s", changed)
    except Exception:
        logger.exception("sync builtin tags failed")
