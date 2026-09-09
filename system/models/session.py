#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""用户会话模型：登录即登记，在线用户列表的统一数据源。

历史边界：在线判定依赖 WS 心跳（Redis ZSET），未建 WS 的纯 HTTP 会话不可枚举。
本模型补齐：登录/WS 接入时各登记一条会话；HTTP 会话经认证链路节流刷新
last_active，WS 会话以 channel 存活为准（get_online_info）；强制下线/登出
置 OFFLINE。channel_name 为空即纯 HTTP 会话。

token 侧的会话级失效：登录签发 token 时写入自定义 claim ``sid``（= 会话 pk，
refresh 轮换/access 派生均自动继承），单会话下线经 SessionTokenRevokedCache
按 sid 拒绝（common/core/auth.py ServerAccessToken.verify 校验）。
"""

import uuid

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel
from system.models.log import UserLoginLog


class UserSession(DbAuditModel):
    """用户会话（登录即登记；在线用户管理的统一数据源）"""

    class Status(models.TextChoices):
        ONLINE = "ONLINE", _("Online")
        OFFLINE = "OFFLINE", _("Offline")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(_("Status"), choices=Status.choices, default=Status.ONLINE, max_length=16, db_index=True)
    # WS 会话的 channel；纯 HTTP 会话为空串（在线判定走 last_active 窗口）
    channel_name = models.CharField(_("WS channel"), max_length=128, blank=True, default="")
    ipaddress = models.CharField(_("Login IP"), max_length=128, blank=True, default="")
    city = models.CharField(_("Login city"), max_length=128, blank=True, default="")
    browser = models.CharField(_("Browser"), max_length=128, blank=True, default="")
    system = models.CharField(_("System"), max_length=128, blank=True, default="")
    agent = models.CharField(_("User agent"), max_length=512, blank=True, default="")
    login_type = models.IntegerField(
        _("Login type"), choices=UserLoginLog.LoginTypeChoices.choices, default=UserLoginLog.LoginTypeChoices.UNKNOWN
    )
    last_active = models.DateTimeField(_("Last active"), default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-last_active"]
        verbose_name = _("User session")
        indexes = [models.Index(fields=["status", "last_active"], name="idx_session_status_active")]

    def __str__(self):
        return f"{self.creator}({self.status})"

    def mark_offline(self):
        if self.status != self.Status.OFFLINE:
            self.status = self.Status.OFFLINE
            self.save(update_fields=["status", "updated_time"])

    @classmethod
    def touch(cls, session_pk, gate_seconds=60):
        """节流刷新活跃时间：Redis 门控 60s 一次，避免每请求写库。

        返回 True 表示本次确实刷新了（供测试断言）；会话不存在/已下线时静默跳过。
        """
        from django.core.cache import cache

        gate_key = f"session_touch_{session_pk}"
        if cache.get(gate_key):
            return False
        cache.set(gate_key, True, timeout=gate_seconds)
        cls.objects.filter(pk=session_pk, status=cls.Status.ONLINE).update(last_active=timezone.now())
        return True
