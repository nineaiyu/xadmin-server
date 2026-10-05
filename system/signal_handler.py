#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : signal_handler.py
# author : ly_13
# date : 12/15/2023
"""platform 域信号接收器：菜单 / 配置 / 字典 / 标签 / 授权规则缓存失效 + 审批回写。

用户/角色/部门缓存失效接收器已随 identity 域拆分（identity/signal_handler.py）；
DataMaskRule 脱敏缓存失效已随 audit 域拆分（audit/signal_handler.py）；
TaskExecution 日志清理随 task 域各自归位。
"""

from django.db.models.signals import m2m_changed, post_delete, post_migrate, post_save, pre_delete
from django.dispatch import receiver

from common.cache.storage import UserSystemConfigCache
from common.core.config import SysConfig
from common.core.filter import invalidate_data_permission_grants_cache
from common.utils import get_logger
from system.models import (
    DataDict,
    DataPermission,
    Menu,
    MenuMeta,
    SystemConfig,
    TaskExecution,
    UserPersonalConfig,
)
from system.signal import approval_instance_finished
from system.utils.platform.dict import invalid_dict_cache

logger = get_logger(__name__)

# m2m 直改（绕过 API 不触发实例 save）同样需要失效权限缓存
M2M_CHANGED_ACTIONS = ("post_add", "post_remove", "post_clear")


@receiver([post_save, pre_delete], sender=Menu)
def clean_cache_handler(sender, instance, **kwargs):
    from identity.services import invalidate_menu_user_caches

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
        from identity.services import invalidate_menu_user_caches

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


@receiver(pre_delete, sender=TaskExecution)
def delete_task_execution_log_handler(sender, **kwargs):
    # 执行历史删除（含批量删除）时联动清理落盘日志文件
    instance = kwargs.get("instance")
    if instance:
        from common.base.utils import remove_file
        from common.celery.utils import get_celery_task_log_path

        remove_file(get_celery_task_log_path(str(instance.pk)))


@receiver([post_save, pre_delete], sender=DataDict)
def invalid_dict_cache_handler(sender, instance, **kwargs):
    # 字典类型变更失效该 code 缓存；类型删除需失效其下字典项缓存，全量失效更稳
    if instance.parent_id:
        invalid_dict_cache(instance.parent.code)
    else:
        invalid_dict_cache()
    logger.info(f"invalid dict cache {instance}")


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


@receiver([post_save, post_delete], sender="system.Tag", dispatch_uid="system.signal_handler.clean_tag_metadata_cache")
def clean_tag_metadata_cache_handler(sender, instance, **kwargs):
    """标签变更：下拉选项缓存 + 元数据载荷缓存同源失效。

    标签名单在构建 user 等列表元数据时被固化进载荷（TagChoiceFilter choices），
    元数据载荷缓存后「新建标签立即可选」需要随变更失效，否则最长 5min（TTL）
    不可选——并行 e2e 实测踩中（预热缓存 + 新建标签 → 下拉缺项）。ORM 直改
    （内置标签同步）也走此信号，故挂模型而非视图。
    """
    from common.core.modelset.metadata import invalidate_metadata_payload_cache
    from system.utils.platform.tags import invalidate_tag_options_cache

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
