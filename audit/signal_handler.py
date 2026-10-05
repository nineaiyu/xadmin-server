#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""audit 域信号处理器：DataMaskRule 变更联动脱敏缓存失效（自 system/signal_handler.py 迁入）。"""

import logging

from django.db.models.signals import m2m_changed, post_save, pre_delete
from django.dispatch import receiver

from audit.models import DataMaskRule
from audit.utils.mask import invalid_mask_cache

logger = logging.getLogger(__name__)

M2M_CHANGED_ACTIONS = ("post_add", "post_remove", "post_clear")


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
