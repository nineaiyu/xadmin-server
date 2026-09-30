#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""外部 MCP 服务器（MCP client 侧接入配置）。

xadmin 作为 MCP 客户端外接第三方 MCP 服务器（Streamable HTTP）：

- 工具清单经「同步」拉取并快照展示（``tools_snapshot``，含名称/描述/参数 schema 摘要）；
- 调用必须命中显式勾选的工具白名单（``allowed_tools`` 为空 = 全部禁止，fail-closed）；
- 出站地址与 Webhook 共用同一守卫（``common/utils/outbound.py``）：https 强制
  （loopback http 仅联调例外），内网目标须经 ``OUTBOUND_ALLOWED_HOSTS`` 白名单登记；
  实际请求走固定解析连接（``pinned_request``，消除 DNS rebinding）；
- 鉴权令牌值级加密落库（signer），回显只给 ``auth_token_set`` 布尔。
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.base.utils import signer
from common.core.models import DbAuditModel, DbUuidModel


class McpServer(DbAuditModel, DbUuidModel):
    """外部 MCP 服务器配置（MCP client）：连接参数 + 工具白名单 + 同步快照。"""

    name = models.CharField(_("Server name"), max_length=64, unique=True)
    url = models.CharField(_("Server URL"), max_length=512, help_text=_("Streamable HTTP endpoint"))
    auth_header = models.CharField(
        _("Auth header"), max_length=64, blank=True, default="", help_text=_("e.g. Authorization")
    )
    auth_token = models.TextField(_("Auth token"), blank=True, default="")
    timeout = models.IntegerField(_("Timeout (seconds)"), default=30)
    # 工具白名单：为空 = 不允许调用任何工具（同步仅展示清单，调用 fail-closed）
    allowed_tools = models.JSONField(_("Allowed tools"), default=list, blank=True)
    enabled = models.BooleanField(_("Enabled"), default=True, db_index=True)
    # 同步快照：[{name, description, read_only}]，仅展示用（调用前会再校验白名单）
    tools_snapshot = models.JSONField(_("Tools snapshot"), default=list, blank=True)
    last_synced_time = models.DateTimeField(_("Last synced time"), null=True, blank=True)
    last_sync_error = models.CharField(_("Last sync error"), max_length=255, blank=True, default="")
    remark = models.CharField(_("Remark"), max_length=255, blank=True, default="")

    class Meta:
        db_table = "system_mcpserver"
        verbose_name = _("External MCP server")
        verbose_name_plural = _("External MCP servers")
        ordering = ("name",)

    def __str__(self):
        return self.name

    @property
    def auth_token_plain(self) -> str:
        """解密后的鉴权令牌（容错：解密失败按未配置处理，不炸调用链）。"""
        if not self.auth_token:
            return ""
        try:
            return signer.decrypt(self.auth_token)
        except Exception:  # noqa: BLE001 历史明文/损坏值按未配置处理
            return ""

    @auth_token_plain.setter
    def auth_token_plain(self, value: str):
        value = (value or "").strip()
        self.auth_token = signer.encrypt(value.encode("utf-8")).decode("utf-8") if value else ""

    @property
    def tool_names(self) -> list:
        """白名单工具名（去空白、去重、保持声明顺序）。"""
        seen, names = set(), []
        for item in self.allowed_tools or []:
            name = str(item or "").strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        return names
