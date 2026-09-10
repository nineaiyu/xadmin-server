#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""新设备/新 IP/新城市登录提醒（异常登录提醒第二维度）。

与既有的异地城市提醒（check_different_city_login_if_need，仅比对最近一次登录
城市）互补：本模块按基线窗口（SECURITY_LOGIN_BASELINE_DAYS，默认 30 天）内的
成功登录历史，比对 (ip, city, browser, system) 四元组逐维度——任一维度首次出现
即提醒。判定与发送全程异常吞掉，绝不影响登录主链路。

配置：SECURITY_ABNORMAL_LOGIN_ALERT_ENABLED（默认 False，渐进启用）。
"""

import datetime
import ipaddress

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from common.utils import get_logger

logger = get_logger(__name__)

# 同用户同维度提醒节流窗口（秒）：24h 内最多提醒一次
ALERT_THROTTLE_SECONDS = 24 * 3600


def _is_private_ip(ip):
    if not ip or ip in ("unknown", "0.0.0.0"):
        return True
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def _detect_new_dimensions(user, ip, city, browser, system):
    """返回本次登录相对基线窗口历史首次出现的维度清单（空列表 = 无异常）。"""
    from system.models import UserLoginLog

    days = int(getattr(settings, "SECURITY_LOGIN_BASELINE_DAYS", 30))
    since = timezone.now() - datetime.timedelta(days=days)
    # 单次查询取回历史四元组（原实现 exists + 3 次 values_list 共 4 次查询）
    history = list(
        UserLoginLog.objects.filter(creator=user, status=True, created_time__gte=since).values_list(
            "ipaddress", "city", "browser", "system"
        )
    )
    # 首次登录（无历史基线）不提醒：注册即首登/新用户场景避免骚扰
    if not history:
        return []

    known_ips = {row[0] for row in history}
    known_cities = {row[1] for row in history}
    known_devices = {(row[2], row[3]) for row in history}
    dimensions = []
    if ip and ip not in ("unknown", "0.0.0.0") and ip not in known_ips:
        dimensions.append("ip")
    if browser or system:
        if (browser, system) not in known_devices:
            dimensions.append("device")
    # 城市维度仅公网登录参与判定：内网 IP 无城市语义（解析值为 LAN），
    # 与 check_different_city_login_if_need 的口径一致
    if city and not _is_private_ip(ip) and city not in known_cities:
        dimensions.append("city")
    return dimensions


def maybe_alert_abnormal_login(user, ip, city, browser, system):
    """登录成功后的异常登录提醒入口（login_success 收敛点调用）。

    全程 try/except 吞掉异常：提醒是附加能力，任何失败只打 warning。
    """
    if not getattr(settings, "SECURITY_ABNORMAL_LOGIN_ALERT_ENABLED", False):
        return
    if user is None or not getattr(user, "pk", None):
        return
    try:
        dimensions = _detect_new_dimensions(user, ip, city, browser, system)
        if not dimensions:
            return
        # 同用户+同维度组合 24h 节流（cache.add 原子占位，仿 failure_handler 范式）
        throttle_key = "abnormal_login_alert_{}_{}".format(user.pk, "_".join(sorted(dimensions)))
        if not cache.add(throttle_key, 1, ALERT_THROTTLE_SECONDS):
            return
        from system.notifications import AbnormalLoginMessage
        from common.utils.timezone import local_now_display

        AbnormalLoginMessage(
            user,
            dimensions,
            {"ip": ip, "city": city, "browser": browser, "system": system, "time": local_now_display()},
        ).publish_async()
        logger.info("Abnormal login alert sent. user: %s dimensions: %s", user.username, dimensions)
    except Exception:  # noqa: BLE001 提醒失败绝不阻断登录
        logger.warning("abnormal login alert failed", exc_info=True)
