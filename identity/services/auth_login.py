#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""登录策略流服务：登录成败处置的唯一收口（口径与视图层解耦）。

覆盖登录链路的三段业务编排：

- 失败处置：失败计数 + IP 封禁 + 失败日志 + 出站 Webhook（``login_failed``）；
- 成功处置：密码/账号有效期拦截、锁定计数清理、登录日志、异地/新设备提醒
  （``login_success``）；
- 收口编排：MFA 判定 → 二次验证响应或成功链路（``complete_login``）。

任何新增登录路径（本地密码 / 验证码 / WebSocket / 第三方 OAuth…）都必须经
:func:`complete_login` 收口：绕过这里等于同时绕过登录 MFA、``UserSession``
登记、登录日志与失败计数清理。
"""

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from audit.services import UserLoginLog
from common.core.response import ApiResponse
from common.utils import get_logger
from common.utils.ip import get_ip_city
from common.utils.request import get_browser, get_os, get_request_ip
from identity.models import UserInfo
from identity.utils.account_expiry import ACCOUNT_EXPIRED_MESSAGE, is_account_expired
from identity.utils.auth import ValidateError, check_different_city_login_if_need, save_login_log
from mfa.services import generate_login_mfa_token, get_login_mfa_methods, is_login_mfa_required
from settings.services import (
    PASSWORD_EXPIRED_MESSAGE,
    LoginBlockUtil,
    LoginIpBlockUtil,
    is_password_expired,
)

logger = get_logger(__name__)


def login_failed(request, username):
    ipaddr = get_request_ip(request)
    login_block_util = LoginBlockUtil(username, ipaddr)
    login_ip_block = LoginIpBlockUtil(ipaddr)
    request.user = UserInfo.objects.filter(username=username).first()
    save_login_log(request, status=False)
    # 出站 Webhook：登录失败事件
    from system.utils.task.webhook import emit_webhook_event

    emit_webhook_event("user.login_failed", {"username": username, "ip": get_request_ip(request)})
    login_block_util.incr_failed_count()
    login_ip_block.set_block_if_need()

    times_remainder = login_block_util.get_remainder_times()
    if times_remainder > 0:
        detail = _(
            "The username or password you entered is incorrect, "
            "please enter it again. "
            "You can also try {times_try} times "
            "(The account will be temporarily locked for {block_time} minutes)"
        ).format(times_try=times_remainder, block_time=settings.SECURITY_LOGIN_LIMIT_TIME)
    else:
        detail = _(
            "The account has been locked (please contact admin to unlock it or try again after {} minutes)"
        ).format(settings.SECURITY_LOGIN_LIMIT_TIME)
    raise ValidateError(detail)


def login_success(request, user_obj, login_type=UserLoginLog.LoginTypeChoices.USERNAME, save_log=True):
    if is_password_expired(user_obj):
        # 密码有效期拦截（SECURITY_PASSWORD_EXPIRATION_DAYS，默认关闭）：MFA 前收口，
        # 待二次验证路径同样拦截；date_password_updated 为空的存量用户宽限放行
        raise ValidateError(PASSWORD_EXPIRED_MESSAGE)
    if is_account_expired(user_obj):
        # 账号有效期拦截：date_expired 为空 = 永不过期；到期由每日任务自动停用 +
        # 到期前提醒，此处拦截覆盖「任务时差窗口内仍可登录」的情形
        raise ValidateError(ACCOUNT_EXPIRED_MESSAGE)
    ipaddr = get_request_ip(request)
    login_block_util = LoginBlockUtil(user_obj.username, ipaddr)
    login_ip_block = LoginIpBlockUtil(ipaddr)
    login_block_util.clean_failed_count()
    login_ip_block.clean_block_if_need()
    if not save_log:
        # 登录 MFA 待验证：密码阶段已通过，锁定计数需清理；登录日志与异地提醒在二次验证通过后记录
        return
    request.user = user_obj
    # 出站 Webhook：登录成功事件（emit 全程吞异常）
    from system.utils.task.webhook import emit_webhook_event

    emit_webhook_event("user.login_succeeded", {"username": user_obj.username, "ip": ipaddr})
    check_different_city_login_if_need(user_obj, ipaddr)
    if login_type != UserLoginLog.LoginTypeChoices.WEBSOCKET:
        # 新设备/新 IP/新城市登录提醒（默认关闭；内部全吞异常，绝不影响登录）。
        # WS 接入不触发：页面伴随登录已提醒过，WS 再提醒只制造重复噪音（计划登记边界）
        from identity.utils.login_alert import maybe_alert_abnormal_login

        maybe_alert_abnormal_login(
            user_obj,
            ipaddr,
            str(get_ip_city(ipaddr) or ""),
            get_browser(request),
            get_os(request),
        )
    save_login_log(request, login_type=login_type)


def evaluate_login_policy_for_request(request, user_obj, ipaddr):
    """登录访问策略判定：返回 (force_mfa, reject_detail)。

    命中结果写 ``request.login_policy_result``（由 save_login_log 落入登录日志）；
    reject 时调用方需自行记失败日志并返回可读文案（含策略名）。
    """
    from identity.utils.login_policy import evaluate_login_policy

    policy = evaluate_login_policy(user_obj, ipaddr)
    if policy.get("result"):
        request.login_policy_result = policy["result"]
    if policy.get("action") == "reject":
        return False, str(_("Login is not allowed by policy: {}").format(policy.get("policy") or ""))
    return policy.get("action") == "require_mfa", ""


def login_mfa_if_required(request, user_obj, force_mfa=False):
    """用户开启登录 MFA 时返回 True：清理密码阶段锁定计数（登录日志在二次验证通过后记录）。

    ``force_mfa``：登录访问策略要求二次验证——无可用方式时降级放行避免登录死锁。
    """
    required = is_login_mfa_required(user_obj)
    if not required and force_mfa:
        if get_login_mfa_methods(user_obj, request):
            required = True
        else:
            logger.warning("Login policy requires MFA but no available method, skip. user: %s", user_obj.username)
    if not required:
        return False
    if not get_login_mfa_methods(user_obj, request):
        # 已开启 MFA 但无可用验证方式（如管理员关闭了全部方式），降级放行避免登录死锁
        logger.warning("Login MFA required but no available method, skip. user: %s", user_obj.username)
        return False
    login_success(request, user_obj, save_log=False)
    return True


def complete_login(request, user_obj, login_type=UserLoginLog.LoginTypeChoices.USERNAME, force_mfa=False):
    """登录成功后的**唯一收口**：MFA 判定 → 会话登记 / 登录日志 / 异常提醒 / 锁定计数清理。

    任何新增登录路径（本地密码 / 验证码 / WebSocket / 第三方 OAuth…）都必须调用它：
    绕过这里等于同时绕过登录 MFA、``UserSession`` 登记、登录日志与失败计数清理
    （历史教训：MFA 只在本地密码链路生效时，新增登录方式就是一条后门）。

    :return: 需要 MFA 二次验证时返回可直接下发的 ``ApiResponse``；否则返回 ``None``，
             由调用方继续下发自己的 token 载荷。
    """
    if login_mfa_if_required(request, user_obj, force_mfa=force_mfa):
        return ApiResponse(
            data={
                "mfa_required": True,
                "mfa_token": generate_login_mfa_token(user_obj),
                "methods": get_login_mfa_methods(user_obj, request),
            }
        )
    login_success(request, user_obj, login_type=login_type)
    return None
