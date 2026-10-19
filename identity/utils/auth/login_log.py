#!/usr/bin/env python
# -*- coding: utf-8 -*-
# project : xadmin-server
# filename : auth_login_log
"""登录日志、短信/邮件验证码核验与异地登录提醒。

``save_login_log`` 写审计域登录日志（LoginLogSerializer 调用期惰性导入，避免
``identity`` → ``audit.serializers`` 的导入环）；``verify_sms_email_code`` 是
验证码类登录/注册/重置链路的前置核验（失败计数与失败日志在此收口）。
"""

import ipaddress
from typing import Any

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from audit.services import UserLoginLog
from common.utils.ip import get_ip_city
from common.utils.request import get_browser, get_os, get_request_ip, get_user_agent
from common.utils.verify_code import CodeError, CodeExpired, SendAndVerifyCodeUtil, TokenTempCache
from identity.models import UserInfo
from identity.notifications import DifferentCityLoginMessage
from identity.utils.auth.block import check_is_block
from identity.utils.auth.captcha import ValidateError
from settings.services import LoginIpBlockUtil


def save_login_log(
    request: Any, login_type: Any = UserLoginLog.LoginTypeChoices.USERNAME, status: Any = True, channel_name: Any = ""
) -> None:
    login_ip = get_request_ip(request) if request else ""
    login_ip = login_ip or "0.0.0.0"
    login_city = get_ip_city(login_ip) or _("Unknown")
    data = {
        "ipaddress": login_ip,
        "city": str(login_city),
        "browser": get_browser(request),
        "system": get_os(request),
        "channel_name": channel_name or getattr(request, "channel_name", ""),
        "status": status,
        "agent": str(get_user_agent(request)) if request else "",
        "login_type": login_type,
        # 登录访问策略命中结果（登录视图写入 request.login_policy_result）
        "policy_result": str(getattr(request, "login_policy_result", "") or "")[:128],
    }
    from audit.serializers.log import LoginLogSerializer  # 审计域序列化器，调用期惰性

    serializer = LoginLogSerializer(data=data, ignore_field_permission=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()


def verify_sms_email_code(request: Any, block_utils: Any) -> Any:
    verify_token = request.data.get("verify_token")
    verify_code = request.data.get("verify_code")
    ipaddr = get_request_ip(request)
    ip_block = LoginIpBlockUtil(ipaddr)

    if not verify_token or not verify_code:
        raise ValidateError(_("Operation failed. Abnormal data"))

    data = TokenTempCache.validate_cache_token(verify_token)
    if not data:
        ip_block.set_block_if_need()
        raise ValidateError(_("Token is invalid or expired"))

    target = data.get("target")
    query_key = data.get("query_key")
    check_is_block(target, ipaddr, login_block=block_utils)
    block_util = block_utils(target, ipaddr)

    try:
        SendAndVerifyCodeUtil(target).verify(verify_code)
    except Exception as e:
        block_util.incr_failed_count()
        ip_block.set_block_if_need()
        request.user = UserInfo.objects.filter(**{query_key: target}).first()
        save_login_log(request, login_type=UserLoginLog.get_login_type(query_key), status=False)
        times_remainder = block_util.get_remainder_times()
        if times_remainder > 0:
            # 只回显验证码业务异常（CodeError/CodeExpired）的既有文案；缓存后端等
            # 内部异常一律用通用文案，避免把连接串、内部路径带进用户可见提示
            error = str(e) if isinstance(e, (CodeError, CodeExpired)) else _("The verification code is incorrect")
            detail = _(
                "{error} please enter it again. "
                "You can also try {times_try} times "
                "(The account will be temporarily locked for {block_time} minutes)"
            ).format(times_try=times_remainder, block_time=settings.SECURITY_LOGIN_LIMIT_TIME, error=error)
        else:
            detail = _(
                "The account has been locked (please contact admin to unlock it or try again after {} minutes)"
            ).format(settings.SECURITY_LOGIN_LIMIT_TIME)

        raise ValidateError(detail) from e

    return query_key, target, verify_token


def check_different_city_login_if_need(user: Any, ipaddr: Any) -> None:
    if not settings.SECURITY_CHECK_DIFFERENT_CITY_LOGIN or ipaddr == "unknown":
        return

    city_white = [_("LAN"), "LAN"]
    is_private = ipaddress.ip_address(ipaddr).is_private
    if is_private:
        return
    last_user_login = UserLoginLog.objects.exclude(city__in=city_white).filter(creator=user, status=True).first()
    if not last_user_login:
        return

    city = get_ip_city(ipaddr)
    last_city = get_ip_city(last_user_login.ipaddress)
    if city == last_city:
        return

    DifferentCityLoginMessage(user, ipaddr, city).publish_async()
