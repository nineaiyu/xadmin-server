#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Webhook 通知渠道设置视图：retrieve 回显 / partialUpdate 保存 / create 连通性测试。

``POST`` 即「测试」：按表单值 ∪ 已存配置的生效快照，向目标端点投递一条测试
载荷（出站走 pinned_request 统一出口，与真实投递同一条链路——测试通过即代表
网络可达与验签前提成立）。测试失败返回可展示的原因（出站守卫拒绝 / HTTP 非 2xx /
超时），不回显堆栈。
"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework.request import Request

from common.core.response import ApiResponse
from common.utils import get_logger
from settings.serializers.notify_webhook import WebhookNotifySettingSerializer
from settings.utils.test_connection import build_test_values
from settings.views.settings import BaseSettingViewSet

logger = get_logger(__name__)

_SETTINGS_KEYS = ["WEBHOOK_ENABLED", "WEBHOOK_URL", "WEBHOOK_SECRET"]
_SECRET_KEYS = {"WEBHOOK_SECRET"}


def _test_delivery(url: str, secret: str) -> str:
    """按生效配置投递一条测试载荷：返回可直接展示的结果文本。"""
    from common.utils.outbound import OutboundBlocked, pinned_request
    from notifications.backends.webhook import DELIVER_TIMEOUT, sign_webhook_payload
    from task.services import outbound_allowed_hosts

    body = b'{"subject": "webhook notification test", "message": "This is a test delivery from xadmin", "users": []}'
    headers = {"Content-Type": "application/json"}
    if secret:
        signature, timestamp = sign_webhook_payload(secret, body)
        headers["X-Webhook-Signature"] = signature
        headers["X-Webhook-Timestamp"] = str(timestamp)
    try:
        response = pinned_request(
            "POST",
            url,
            allow_private=False,
            allowed_hosts=outbound_allowed_hosts(),
            data=body,
            headers=headers,
            timeout=DELIVER_TIMEOUT,
        )
    except OutboundBlocked as exc:
        return str(_("Target rejected by outbound guard: {}")).format(exc)
    except Exception as exc:  # noqa: BLE001 测试入口兜底，不回显堆栈
        logger.warning("Webhook notify test unexpected error", exc_info=True)
        return str(exc)
    if 200 <= int(getattr(response, "status_code", 0) or 0) < 300:
        return str(_("OK"))
    return str(_("Endpoint returned HTTP {}")).format(getattr(response, "status_code", "?"))


class WebhookNotifySettingViewSet(BaseSettingViewSet):
    """Webhook 通知渠道"""

    serializer_class = WebhookNotifySettingSerializer
    category = "notify_webhook"

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Any:
        """测试{cls}"""
        serializer = self.get_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)

        # 测试连接统一口径：表单值构造生效配置快照（未提交键回退已存配置、
        # write_only 密文留空沿用已存值），不临时改进程全局 settings
        values = build_test_values(
            serializer.validated_data,
            request.data,
            keys=_SETTINGS_KEYS,
            secret_keys=_SECRET_KEYS,
        )
        if not values.get("WEBHOOK_ENABLED"):
            return ApiResponse(code=1002, detail=str(_("Disabled")))
        url = values.get("WEBHOOK_URL") or ""
        if not url:
            return ApiResponse(code=1002, detail=_("Missing configuration: WEBHOOK_URL"))
        # 测试载荷走真实出站链路（与渠道 send_msg 同一条 pinned_request 路径）
        result = _test_delivery(url, str(values.get("WEBHOOK_SECRET") or ""))
        if result != str(_("OK")):
            return ApiResponse(code=1002, detail=result)
        return ApiResponse(detail=_("Test delivery delivered to the endpoint, please check"))
