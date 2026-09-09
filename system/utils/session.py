#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""会话管理：登录登记、强制下线（按用户踢全部会话）、单会话下线、过期清理。

历史边界：send_logout_msg 只关 WS 连接并让前端本地登出，被踢用户的 access token
在剩余有效期内仍可调 API，且纯 HTTP 会话（未建 WS）不可枚举。本模块补齐：
- 用户级失效时间戳（force_logout_user，踢全部会话）；
- UserSession 登录登记 + token claim ``sid`` 绑定（单会话可精确失效）；
- 在线用户列表统一数据源（WS 会话按 channel 存活，HTTP 会话按 last_active 窗口）。
"""

from django.utils import timezone

from django.utils.translation import gettext_lazy as _

from common.cache.storage import UserTokenRevokedCache
from common.utils import get_logger

logger = get_logger(__name__)


def force_logout_user(user_pk, operator=None):
    """强制某用户全部会话下线，返回被踢掉的 WS channel 数。

    ① 写用户级令牌失效时间戳：iat 早于该值的 access token 一律拒绝
      （ServerAccessToken.verify 校验，common/core/auth.py）；
    ② 拉黑该用户全部 refresh token，防止被踢后立刻刷新续命；
    ③ WS 推送 logout 消息并断开连接（前端收到即本地登出）；
    ④ 登记的 UserSession 全部置 OFFLINE（在线列表立即消失）。
    """
    from message.services import get_online_users_layers, send_logout_msg
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    from system.models import UserSession

    # ① 服务端 access token 失效（时间戳取当前秒；iat 与 exp 均为 epoch 秒）
    UserTokenRevokedCache(user_pk).set_storage_cache(int(timezone.now().timestamp()))

    # ② refresh token 全部拉黑（OutstandingToken 为登录/轮换全量签发记录）
    tokens = OutstandingToken.objects.filter(user_id=user_pk)
    for token in tokens:
        BlacklistedToken.objects.get_or_create(token=token)

    # ③ 踢掉在线 WS 连接（get_online_users_layers 批量接口：入参列表、返回 {user_pk: [channels]}）
    channels = get_online_users_layers([user_pk]).get(user_pk, [])
    if channels:
        send_logout_msg(user_pk, channels)

    # ④ 会话记录置离线（channel 已随 ③ 断开）
    UserSession.objects.filter(creator_id=user_pk, status=UserSession.Status.ONLINE).update(
        status=UserSession.Status.OFFLINE
    )
    logger.info("force logout user %s: %s channels, operator %s", user_pk, len(channels), operator)
    return len(channels)


def register_user_session(request, user, login_type, channel_name=""):
    """登录/WS 接入时登记会话，返回 UserSession 实例。

    元数据（ip/city/browser/system/agent）与登录日志（save_login_log）同口径
    取自 request；调用方对异常自行兜底——会话管理属附加能力，不影响登录主流程。
    """
    from common.utils.ip import get_ip_city
    from common.utils.request import get_browser, get_os, get_request_ip, get_user_agent
    from system.models import UserSession

    login_ip = get_request_ip(request) if request else ""
    return UserSession.objects.create(
        creator=user,
        channel_name=channel_name or "",
        ipaddress=login_ip or "0.0.0.0",
        city=str(get_ip_city(login_ip) or _("Unknown")),
        browser=get_browser(request) if request else "",
        system=get_os(request) if request else "",
        agent=str(get_user_agent(request)) if request else "",
        login_type=login_type,
    )


def bind_session_claim(refresh_token, session_pk):
    """把会话 pk 写入 refresh token 自定义 claim ``sid``，返回 (refresh_str, access_str)。

    access 由 refresh 派生并继承自定义 claim；refresh 轮换（ROTATE_REFRESH_TOKENS）
    也保留 sid，因此单会话失效对续命后的新 token 同样生效。
    """
    refresh_token["sid"] = str(session_pk)
    return str(refresh_token), str(refresh_token.access_token)


def expire_stale_sessions():
    """把活跃窗口（SESSION_ONLINE_TIMEOUT）外的 HTTP 会话置 OFFLINE，返回处理行数。

    WS 会话不在此处置离线：其在线与否由 channel 存活决定（在线列表查询时判定），
    优雅断开由 websocket logout 钩子标记，异常残留由保留期清理任务回收。
    """
    from datetime import timedelta

    from common.core.config import SysConfig

    from system.models import UserSession

    cutoff = timezone.now() - timedelta(seconds=SysConfig.SESSION_ONLINE_TIMEOUT)
    return UserSession.objects.filter(status=UserSession.Status.ONLINE, channel_name="", last_active__lt=cutoff).update(
        status=UserSession.Status.OFFLINE
    )


def clean_expired_sessions():
    """删除超过保留期（USER_SESSION_RETENTION_DAYS）的会话记录，分批删除，返回行数。"""
    from datetime import timedelta

    from common.core.config import SysConfig

    from system.models import UserSession

    retention_days = SysConfig.USER_SESSION_RETENTION_DAYS
    if retention_days <= 0:
        return 0
    deadline = timezone.now() - timedelta(days=retention_days)
    removed = 0
    while True:
        pks = list(UserSession.objects.filter(last_active__lt=deadline).values_list("pk", flat=True)[:2000])
        if not pks:
            break
        removed += UserSession.objects.filter(pk__in=pks).delete()[0]
    return removed
