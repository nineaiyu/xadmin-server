#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""账号安全风险巡检与处置（F-6）。

检测函数与登录拦截**同源**（`settings.services` 的密码套件），避免「巡检说没问题、
登录被拦」；风险项按「用户 × 风险类型」幂等维护：

- 新发现 → 创建 PENDING；
- 仍存在且 PENDING → 刷新等级 / 明细；
- 仍存在但 RESOLVED（已处置过）→ **重新置 PENDING**（风险复现需要再处理）；
- 仍存在且 IGNORED → 保持（人工豁免不打扰）；
- 已消失且 PENDING → 自动置 RESOLVED（remark 注明自动解除）。

处置动作：通知本人 / 强制改密（登录后引导改密）/ 强制下线 / 停用账号 / 豁免 / 关闭，
全部记录处置人与时间（留痕口径与审计一致）。
"""

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from settings.services import is_password_expired

logger = get_logger(__name__)

# 处置动作清单（前端按钮与元数据同源）
HANDLE_ACTIONS = ("notify", "force_change_password", "force_logout", "disable", "ignore", "resolve")


def _collect_risks(now):
    """产出当前全部风险项：[{"user", "risk_type", "level", "detail"}]。"""
    from system.models import AccountRisk, UserInfo

    idle_days = int(getattr(settings, "SECURITY_ACCOUNT_IDLE_DAYS", 90) or 0)
    stale_days = int(getattr(settings, "SECURITY_PASSWORD_STALE_DAYS", 180) or 0)
    max_superusers = int(getattr(settings, "SECURITY_SUPERUSER_MAX_COUNT", 5) or 0)

    items = []
    users = UserInfo.objects.all().only(
        "pk",
        "username",
        "nickname",
        "last_login",
        "date_joined",
        "is_active",
        "is_superuser",
        "mfa_level",
        "otp_secret_key",
        "date_password_updated",
    )
    for user in users:
        if is_password_expired(user):
            items.append(
                {
                    "user": user,
                    "risk_type": AccountRisk.RiskType.PASSWORD_EXPIRED,
                    "level": AccountRisk.Level.HIGH,
                    "detail": {
                        "description": str(_("The password has expired but is still in use")),
                        "suggestion": str(_("Notify the user or force a password change")),
                        "date_password_updated": user.date_password_updated.isoformat()
                        if user.date_password_updated
                        else "",
                    },
                }
            )
        if stale_days > 0:
            anchor = user.date_password_updated or user.date_joined
            if anchor and (now - anchor).days >= stale_days:
                items.append(
                    {
                        "user": user,
                        "risk_type": AccountRisk.RiskType.PASSWORD_STALE,
                        "level": AccountRisk.Level.MEDIUM,
                        "detail": {
                            "description": str(_("The password has not been changed for {} days")).format(stale_days),
                            "suggestion": str(_("Notify the user to change the password")),
                            "days": (now - anchor).days,
                        },
                    }
                )
        if idle_days > 0 and user.is_active:
            if user.last_login is None:
                if (now - user.date_joined).days >= idle_days:
                    items.append(
                        {
                            "user": user,
                            "risk_type": AccountRisk.RiskType.NEVER_LOGGED_IN,
                            "level": AccountRisk.Level.MEDIUM,
                            "detail": {
                                "description": str(_("The account has never logged in since creation")),
                                "suggestion": str(_("Confirm whether the account is still needed, or disable it")),
                                "days": (now - user.date_joined).days,
                            },
                        }
                    )
            elif (now - user.last_login).days >= idle_days:
                items.append(
                    {
                        "user": user,
                        "risk_type": AccountRisk.RiskType.LOGIN_STALE,
                        "level": AccountRisk.Level.LOW,
                        "detail": {
                            "description": str(_("No login for {} days")).format(idle_days),
                            "suggestion": str(_("Confirm whether the account is still needed, or disable it")),
                            "days": (now - user.last_login).days,
                        },
                    }
                )
        if user.is_superuser and user.is_active and not user.mfa_enabled:
            items.append(
                {
                    "user": user,
                    "risk_type": AccountRisk.RiskType.SUPERUSER_NO_MFA,
                    "level": AccountRisk.Level.HIGH,
                    "detail": {
                        "description": str(_("The administrator has not bound MFA")),
                        "suggestion": str(_("Guide the administrator to bind OTP or Passkey")),
                    },
                }
            )
    if max_superusers > 0:
        count = UserInfo.objects.filter(is_superuser=True, is_active=True).count()
        if count > max_superusers:
            items.append(
                {
                    "user": None,
                    "risk_type": AccountRisk.RiskType.SUPERUSER_COUNT,
                    "level": AccountRisk.Level.MEDIUM,
                    "detail": {
                        "description": str(_("The number of active administrators exceeds the threshold ({})")).format(
                            max_superusers
                        ),
                        "suggestion": str(_("Review the administrator list and remove redundant accounts")),
                        "count": count,
                        "threshold": max_superusers,
                    },
                }
            )
    return items


def scan_account_risks(operator=None) -> dict:
    """执行一次巡检（幂等），返回 {"created", "updated", "resolved", "total"}。"""
    from system.models import AccountRisk
    from system.utils.approval.display import user_display

    now = timezone.now()
    items = _collect_risks(now)
    seen = set()
    created = updated = 0
    for item in items:
        user = item["user"]
        seen.add((user.pk if user else None, item["risk_type"]))
        row = AccountRisk.objects.filter(user=user, risk_type=item["risk_type"]).first()
        fields = {
            "level": item["level"],
            "detail": item["detail"],
            "user_display": user_display(user) if user else str(_("Global")),
        }
        if row is None:
            AccountRisk.objects.create(user=user, risk_type=item["risk_type"], **fields)
            created += 1
            continue
        if row.status == AccountRisk.Status.IGNORED:
            # 人工豁免：保持不打扰
            continue
        if row.status == AccountRisk.Status.RESOLVED:
            # 风险复现：重新置为待处理
            for key, value in fields.items():
                setattr(row, key, value)
            row.status = AccountRisk.Status.PENDING
            row.remark = ""
            row.handled_by = None
            row.handled_at = None
            row.save(update_fields=[*fields, "status", "remark", "handled_by", "handled_at", "updated_time"])
            updated += 1
            continue
        for key, value in fields.items():
            setattr(row, key, value)
        row.save(update_fields=[*fields, "updated_time"])
        updated += 1

    resolved = 0
    for row in AccountRisk.objects.filter(status=AccountRisk.Status.PENDING):
        if (row.user_id, row.risk_type) not in seen:
            row.status = AccountRisk.Status.RESOLVED
            row.remark = str(_("Automatically resolved (risk no longer detected)"))[:255]
            row.handled_at = now
            row.save(update_fields=["status", "remark", "handled_at", "updated_time"])
            resolved += 1
    result = {"created": created, "updated": updated, "resolved": resolved, "total": len(items)}
    logger.info("account risk scan: %s (operator=%s)", result, getattr(operator, "username", None))
    return result


def _notify_user(user, title, message, level="info"):
    from notifications.message import SiteMessageUtil

    try:
        if level == "error":
            SiteMessageUtil.notify_error(users=user, title=title, message=message)
        elif level == "success":
            SiteMessageUtil.notify_success(users=user, title=title, message=message)
        else:
            SiteMessageUtil.notify_info(users=user, title=title, message=message)
    except Exception:  # noqa: BLE001 通知失败不影响处置留痕
        logger.warning("notify account risk handler failed. user:%s", getattr(user, "pk", None), exc_info=True)


def handle_account_risk(risk, action, operator=None, remark="") -> tuple:
    """处置一项风险，返回 (是否成功, 说明文案)。"""
    from system.models import AccountRisk
    from system.utils.session import force_logout_user

    if action not in HANDLE_ACTIONS:
        return False, str(_("Unsupported action"))
    user = risk.user
    if action in ("notify", "force_change_password", "force_logout", "disable") and user is None:
        return False, str(_("The user of this risk item no longer exists"))

    try:
        if action == "notify":
            _notify_user(
                user,
                str(_("Account security reminder")),
                str(_("Your account has a security risk: {}")).format(
                    (risk.detail or {}).get("description", risk.get_risk_type_display())
                ),
            )
        elif action == "force_change_password":
            user.must_change_password = True
            user.save(update_fields=["must_change_password"])
            _notify_user(
                user,
                str(_("Password change required")),
                str(_("Your account requires a password change, please change it as soon as possible")),
            )
        elif action == "force_logout":
            force_logout_user(user.pk, operator=operator)
        elif action == "disable":
            user.is_active = False
            user.save(update_fields=["is_active"])
            force_logout_user(user.pk, operator=operator)
        elif action == "ignore":
            risk.status = AccountRisk.Status.IGNORED
        elif action == "resolve":
            risk.status = AccountRisk.Status.RESOLVED
    except Exception as exc:  # noqa: BLE001 处置异常归一为可读失败，不抛 500
        logger.exception("handle account risk failed. risk:%s action:%s", risk.pk, action)
        return False, str(exc)[:200]

    if action in ("ignore", "resolve"):
        risk.handled_by = operator
        risk.handled_at = timezone.now()
        risk.remark = (remark or risk.remark or "")[:255]
        risk.save(update_fields=["status", "handled_by", "handled_at", "remark", "updated_time"])
    else:
        # 处置动作执行成功即留痕；风险项保持 PENDING（待风险消失后自动解除）
        risk.handled_by = operator
        risk.handled_at = timezone.now()
        if remark:
            risk.remark = remark[:255]
        risk.save(update_fields=["handled_by", "handled_at", "remark", "updated_time"])
    return True, ""
