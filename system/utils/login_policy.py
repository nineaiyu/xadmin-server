#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""登录访问策略判定（F-7）。

语义（对标 JumpServer LoginACL，落地为本仓的「首个命中生效」简化模型）：

- 策略按 ``priority`` 升序（同优先级按创建时间）求值，**首个条件全部命中的策略**决定动作：
  ``accept`` 放行（可作白名单窗口）、``reject`` 拒绝、``require_mfa`` 要求二次验证、
  ``record`` 仅记录；
- 条件维度为空 = 该维度不限制；``ip_ranges`` 非空但拿不到登录 IP 时视为不匹配
  （避免「限制网段」被伪造/缺失 IP 绕过）；
- 判定发生在**密码校验通过之后**（避免匿名探测策略信息），结果写入登录日志。

典型配置（「仅工作时间可登录」）：
    ① priority=10，weekdays=[1..5]，start_time=09:00，end_time=18:00，action=accept；
    ② priority=100，target_type=all，action=reject。
"""

from datetime import datetime

from django.utils import timezone

from common.utils import get_logger
from common.utils.ip.utils import contains_ip

logger = get_logger(__name__)


def _split_values(raw):
    return [item.strip() for item in str(raw or "").replace("\n", ",").split(",") if item.strip()]


def _user_role_codes(user):
    try:
        return set(user.roles.values_list("code", flat=True))
    except Exception:  # noqa: BLE001 角色查询异常不阻断登录（视为无角色）
        return set()


def match_target(policy, user) -> bool:
    """对象维度匹配：全体 / 指定用户（用户名） / 指定角色（角色 code）。"""
    if policy.target_type == policy.TargetType.ALL:
        return True
    values = _split_values(policy.target_value)
    if not values:
        # 声明了对象维度却没给值 = 不匹配（fail-closed，避免策略静默变全体）
        return False
    if policy.target_type == policy.TargetType.USER:
        return str(user.username) in values
    return bool(_user_role_codes(user) & set(values))


def match_time(policy, when: datetime) -> bool:
    """时段维度匹配：星期（1=周一..7=周日）+ 起止时间（start > end 表示跨天）。"""
    weekdays = [int(day) for day in (policy.weekdays or []) if str(day).strip().isdigit()]
    if weekdays and when.isoweekday() not in weekdays:
        return False
    if policy.start_time and policy.end_time:
        current = when.time()
        if policy.start_time <= policy.end_time:
            return policy.start_time <= current <= policy.end_time
        # 跨天窗口（如 22:00-06:00）
        return current >= policy.start_time or current <= policy.end_time
    return True


def match_ip(policy, ip: str) -> bool:
    """网段维度匹配：每行一个 CIDR / 区间（复用 contains_ip 支持的全部形态）。"""
    ranges = [line.strip() for line in str(policy.ip_ranges or "").splitlines() if line.strip()]
    if not ranges:
        return True
    if not ip:
        return False
    return contains_ip(ip, ranges)


def match_policy(policy, user, ip, when: datetime) -> bool:
    return match_target(policy, user) and match_time(policy, when) and match_ip(policy, ip)


def evaluate_login_policy(user, ip, when=None) -> dict:
    """求值登录策略，返回首个命中结果。

    :return: ``{"action": None|"accept"|"reject"|"require_mfa"|"record",
                "policy": 策略名或 "", "result": "action:policy" 或 ""}``
    """
    from system.models import LoginAccessPolicy

    when = when or timezone.localtime()
    for policy in LoginAccessPolicy.objects.filter(is_active=True).order_by("priority", "created_time"):
        try:
            if match_policy(policy, user, ip, when):
                return {
                    "action": policy.action,
                    "policy": policy.name,
                    "result": f"{policy.action}:{policy.name}"[:128],
                }
        except Exception:  # noqa: BLE001 单条策略异常不影响登录（跳过并告警）
            logger.warning("evaluate login policy failed. policy:%s", policy.pk, exc_info=True)
    return {"action": None, "policy": "", "result": ""}


def preview_login_policy(user, ip, when=None) -> dict:
    """命中预演（管理页用）：返回全部激活策略的逐条匹配结果与最终判定。"""
    from system.models import LoginAccessPolicy

    when = when or timezone.localtime()
    items = []
    final = {"action": None, "policy": ""}
    decided = False
    for policy in LoginAccessPolicy.objects.filter(is_active=True).order_by("priority", "created_time"):
        matched = match_policy(policy, user, ip, when)
        items.append(
            {
                "pk": str(policy.pk),
                "name": policy.name,
                "priority": policy.priority,
                "action": policy.action,
                "matched": matched,
                "effective": bool(matched and not decided),
            }
        )
        if matched and not decided:
            final = {"action": policy.action, "policy": policy.name}
            decided = True
    return {"matched": bool(final["action"]), "action": final["action"], "policy": final["policy"], "items": items}
