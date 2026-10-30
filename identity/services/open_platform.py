#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""开放平台服务：应用凭据生成/校验、回调探测、用量统计。

从 ``identity.views.open.open`` 下沉：这些能力被换发端点、OAuth 端点与管理页
共用，且包含 SSRF 加固与聚合查询；放在视图模块会让视图同时承担协议转换与
业务实现。视图侧保留同名再导出，既有调用路径不变。
"""

import hmac
import json
import secrets
from datetime import timedelta
from typing import Any

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.core.auth import hash_pat_token
from common.core.response import API_SUCCESS_CODE
from identity.models.token import ApiApplication, PersonalAccessToken
from task.services import decrypt_secret, encrypt_secret, loopback_allowed, sign_payload

CLIENT_SECRET_PREFIX = "aps"
CALLBACK_TIMEOUT_SECONDS = 10
#: 多地址串行探测的总预算：单个地址慢也不无限拉长管理页请求（超预算地址按失败明示）
CALLBACK_PROBE_TOTAL_BUDGET_SECONDS = 30


def build_client_credentials() -> tuple[str, str, str, str]:
    """生成 client_id / client_secret 明文与落库哈希、前缀（复用 PAT 的 sha256 口径）。"""
    client_id = f"app_{secrets.token_hex(8)}"
    raw_secret = f"{CLIENT_SECRET_PREFIX}_{secrets.token_urlsafe(32)}"
    return client_id, raw_secret, hash_pat_token(raw_secret), raw_secret[:12]


def build_callback_secret() -> tuple[str, str]:
    """生成回调签名密钥（密文存储，明文只在创建/重置响应返回一次）。"""
    raw_secret = f"apc_{secrets.token_urlsafe(32)}"
    return raw_secret, encrypt_secret(raw_secret)


def verify_application_credentials(client_id: str, client_secret: str) -> Any:
    """校验应用凭据（启用/过期/owner 启用），返回 ``(application, 错误文案)``。

    OAuth token/revoke 与 client-credentials 换发共用；哈希比较用 ``compare_digest``
    防时序侧信道（与 PAT 认证口径一致）。
    """
    application = ApiApplication.objects.filter(client_id=client_id).select_related("creator").first()
    if application is None or not hmac.compare_digest(application.client_secret_hash, hash_pat_token(client_secret)):
        return None, _("Invalid client credentials")
    now = timezone.now()
    if not application.is_active or (application.expired_at and application.expired_at <= now):
        return None, _("Application is disabled or expired")
    if application.creator is None or not application.creator.is_active:
        return None, _("Application owner account is disabled")
    return application, None


def send_test_callback(
    application: ApiApplication, url: str, client: Any = None, timeout: Any = None
) -> dict[str, Any]:
    """向单个回调地址投递一次签名探测（返回值 = 投递结果，供管理页展示）。

    ``timeout`` 缺省用单地址预算（CALLBACK_TIMEOUT_SECONDS）；多地址串行探测时
    由调用方按剩余总预算传入更小值。

    生产路径（client=None）与 webhook 投递链路同口径走 ``pinned_request``：
    发送侧严格校验目标归属（私网/环回/link-local 拒绝，OUTBOUND_ALLOWED_HOSTS
    放行）并把连接固定为已校验 IP——裸 ``requests.post`` 对域名型回调存在 DNS
    rebinding 触达内网的 SSRF 窗口，且探测携带真实 HMAC 签名，绝不能打到内网。
    注入客户端路径（测试离线桩）保持原样调用，不做固定连接。
    """
    secret = decrypt_secret(application.callback_secret_encrypted) if application.callback_secret_encrypted else ""
    body = json.dumps(
        {
            "event": "api_application.test",
            "client_id": application.client_id,
            "name": application.name,
            "timestamp": timezone.now().isoformat(),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    signature, timestamp = sign_payload(secret, body)
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature,
        "X-Webhook-Timestamp": str(timestamp),
    }
    try:
        if client is None:
            from common.utils.outbound import pinned_request
            from task.services import outbound_allowed_hosts

            response = pinned_request(
                "POST",
                url,
                allow_private=False,
                # 与 Webhook 投递同一放行口径：生产 fail-closed，
                # 仅 DEBUG / 显式 WEBHOOK_ALLOW_LOOPBACK 时允许环回联调
                allow_loopback=loopback_allowed(),
                allowed_hosts=outbound_allowed_hosts(),
                data=body,
                headers=headers,
                timeout=timeout or CALLBACK_TIMEOUT_SECONDS,
            )
        else:
            response = client.post(url, data=body, headers=headers, timeout=timeout or CALLBACK_TIMEOUT_SECONDS)
        return {"url": url, "success": 200 <= response.status_code < 300, "status_code": response.status_code}
    except Exception as exc:  # noqa: BLE001 网络异常与拒绝同语义，按失败结果返回不打断管理页
        return {"url": url, "success": False, "detail": str(exc)}


def application_usage_stats(application: ApiApplication, days: int) -> dict[str, Any]:
    """应用用量报表（近 N 天）：聚合 OperationLog（token_pk ∈ 应用全部凭证）。

    凭证只失效不删除（删除应用才级联），故 token_pk 口径覆盖应用全生命周期；
    失败判定与个人令牌 stats 同源（业务码非成功即失败）；配额为软口径读取当日计数缓存。
    """
    from django.core.cache import cache
    from django.db.models import Avg, Count, Q
    from django.db.models.functions import TruncDate

    from audit.services import OperationLog

    since = timezone.now() - timedelta(days=days)
    token_pks = list(PersonalAccessToken.objects.filter(api_application=application).values_list("pk", flat=True))
    queryset = OperationLog.objects.filter(token_pk__in=token_pks, created_time__gte=since)
    failed_q = ~Q(status_code=API_SUCCESS_CODE)
    daily = [
        {
            "date": row["day"].isoformat() if row["day"] else "",
            "total": row["total"],
            "failed": row["failed"],
            "avg_duration": round(row["avg_duration"] or 0, 4),
        }
        for row in queryset.annotate(day=TruncDate("created_time"))
        .values("day")
        .annotate(total=Count("pk"), failed=Count("pk", filter=failed_q), avg_duration=Avg("exec_time"))
        .order_by("day")
    ]
    totals = queryset.aggregate(total=Count("pk"), failed=Count("pk", filter=failed_q), avg_duration=Avg("exec_time"))
    top_paths = list(queryset.values("path").annotate(total=Count("pk")).order_by("-total")[:10])
    status_codes = list(queryset.values("status_code").annotate(total=Count("pk")).order_by("-total")[:10])
    day_key = timezone.now().strftime("%Y%m%d")
    try:
        used_today = cache.get(f"api_app_quota_{application.pk}_{day_key}") or 0
    except Exception:  # noqa: BLE001 缓存故障按 0 展示（报表不阻断）
        used_today = 0
    return {
        "days": days,
        "total": totals["total"] or 0,
        "failed": totals["failed"] or 0,
        "avg_duration": round(totals["avg_duration"] or 0, 4),
        "daily": daily,
        "top_paths": top_paths,
        "status_codes": status_codes,
        "quota": {
            "daily_quota": application.daily_quota or 0,
            "alert_percent": application.quota_alert_percent or 80,
            "used_today": used_today,
        },
    }
