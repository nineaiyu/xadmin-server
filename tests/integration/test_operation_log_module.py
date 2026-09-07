# -*- coding: utf-8 -*-
"""操作日志 module 超长防御回归测试。

OperationLog.module 为 varchar(64)，此前 get_verbose_name 直接取视图类
docstring 全文，长 docstring（mfa.UserConfirmViewSet 的 412 交互说明、
MenuViewSet 的回收站说明）导致这些视图集的 POST 在 ApiLoggingMiddleware
process_view 写占位日志时 DataError，整个请求 500——敏感操作二次验证
（POST /api/mfa/confirm）链路全断。

注：path/body 等字段由 transaction.on_commit 的 UPDATE 回填，pytest 事务
内不提交，因此占位行仅 module 有值。
"""
import pytest

from system.models import OperationLog

pytestmark = pytest.mark.django_db

MFA_CONFIRM_URL = "/api/mfa/confirm"
MENU_URL = "/api/system/menu"


def test_get_verbose_name_takes_first_line_only():
    """长 docstring 只取首行（module 列宽 64，多行文本也不可读）。"""
    from common.utils.request import get_verbose_name
    from mfa.views import UserConfirmViewSet

    __, verbose = get_verbose_name(None, UserConfirmViewSet)
    assert verbose == "敏感操作二次验证"
    assert len(verbose) <= 64


def test_mfa_confirm_not_broken_by_log_middleware(auth_client, superuser):
    """POST /api/mfa/confirm 不再因写日志 500，且明文密码不落操作日志。"""
    # 解绑 OTP 的 412 要求 confirm_type=password，密码方式在该级别可验证
    resp = auth_client.post(
        MFA_CONFIRM_URL,
        {"confirm_type": "password", "method": "password", "code": "Admin@123456"},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.data["code"] == 1000

    # 确认提交体里的 code 是明文登录密码，已在 API_LOG_IGNORE 中排除
    assert not OperationLog.objects.filter(module="敏感操作二次验证").exists()


def test_menu_post_module_truncated_not_500(auth_client, superuser):
    """菜单视图集 docstring 超过 64 字符（另一个踩中视图）：写入截断而非 500。"""
    payload = {
        "name": "e2e-log-module",
        "menu_type": 1,
        "path": "/x/log-module",
        "component": "x/log-module/index",
        "rank": 9999,
        "meta": {"title": "e2e-log-module"},
    }
    resp = auth_client.post(MENU_URL, payload, format="json")
    assert resp.status_code == 200
    assert resp.data["code"] == 1000

    log = OperationLog.objects.order_by("-created_time").first()
    assert log is not None
    assert log.module and len(log.module) <= 64
