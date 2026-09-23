#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : account_expiry
"""账号有效期与到期处置。

- **登录拦截**：``date_expired`` 非空且已过期 → 登录被拒（与密码过期同一拦截面，
  见 ``system/views/auth/login.py::login_success``）；
- **到期提醒**：每日任务对「N 天内到期」的在用账号发站内信 + 邮件
  （N = ``SysConfig.ACCOUNT_EXPIRY_REMIND_DAYS``，0 = 关闭；同一账号同一天最多提醒一次）；
- **到期停用**：每日任务对已过期账号自动 ``is_active=False`` 并通知本人；
  超管可在用户管理里延期后重新启用（双通道提醒 + 人工闭环）。

存量用户 ``date_expired`` 为空 = 永不过期（零影响）。
"""

import datetime

from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.core.config import SysConfig
from common.utils import get_logger

logger = get_logger(__name__)

ACCOUNT_EXPIRED_MESSAGE = _("Account has expired, please contact the administrator")
ACCOUNT_EXPIRING_TITLE = _("Account expiring soon")
ACCOUNT_EXPIRED_TITLE = _("Account disabled because it expired")

_NOTICE_CACHE_PREFIX = "_KEY_ACCOUNT_EXPIRY_NOTICE_{}"
_NOTICE_CACHE_TTL = 86400


def is_account_expired(user) -> bool:
    """账号是否已到期（``date_expired`` 为空 = 永不过期）。"""
    if user is None or getattr(user, "date_expired", None) is None:
        return False
    return user.date_expired <= timezone.now()


def remind_days() -> int:
    return int(getattr(SysConfig, "ACCOUNT_EXPIRY_REMIND_DAYS", 0) or 0)


def _notify(user, title, message) -> None:
    """站内信 + 邮件（渠道失败仅告警，不阻断任务）。"""
    from notifications.message import SiteMessageUtil

    try:
        SiteMessageUtil.notify_info(users=user, title=str(title), message=str(message))
    except Exception:
        logger.warning("send account expiry site message failed. user: %s", user, exc_info=True)
    email = getattr(user, "email", "")
    if email:
        try:
            from common.tasks import send_mail_async

            send_mail_async.delay(str(title), str(message), [email], html_message=str(message))
        except Exception:
            logger.warning("send account expiry mail failed. user: %s", user, exc_info=True)


def notify_expiring_accounts() -> int:
    """提醒「N 天内到期」的在用账号（站内信 + 邮件）；返回本次提醒数。"""
    from system.models import UserInfo

    days = remind_days()
    if days <= 0:
        return 0
    now = timezone.now()
    users = UserInfo.objects.filter(
        is_active=True,
        date_expired__isnull=False,
        date_expired__gt=now,
        date_expired__lte=now + datetime.timedelta(days=days),
    )
    notified = 0
    for user in users:
        if not cache.add(_NOTICE_CACHE_PREFIX.format(user.pk), 1, _NOTICE_CACHE_TTL):
            continue  # 同一天已提醒过（任务重跑 / 多实例部署都不重复打扰）
        remaining = max((user.date_expired - now).days, 0)
        _notify(
            user,
            ACCOUNT_EXPIRING_TITLE,
            _("Your account will expire in %(days)s day(s) (%(expired)s), please contact the administrator to extend")
            % {
                "days": remaining,
                "expired": timezone.localtime(user.date_expired).strftime("%Y-%m-%d %H:%M"),
            },
        )
        notified += 1
    if notified:
        logger.info("notify %s expiring accounts (within %s days)", notified, days)
    return notified


def disable_expired_accounts() -> int:
    """停用已过期账号（``is_active=False``）并通知本人；返回停用数。"""
    from system.models import UserInfo

    now = timezone.now()
    users = list(UserInfo.objects.filter(is_active=True, date_expired__isnull=False, date_expired__lte=now))
    for user in users:
        user.is_active = False
        user.save(update_fields=["is_active"])
        _notify(user, ACCOUNT_EXPIRED_TITLE, ACCOUNT_EXPIRED_MESSAGE)
        logger.info("disable expired account: %s (expired at %s)", user.username, user.date_expired)
    return len(users)
