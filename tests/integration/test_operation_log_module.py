# -*- coding: utf-8 -*-
"""操作日志 module 超长防御回归测试。

OperationLog.module 为 varchar(64)，此前 get_verbose_name 直接取视图类
docstring 全文，长 docstring（mfa.UserConfirmViewSet 的 412 交互说明、
MenuViewSet 的回收站说明）导致这些视图集的 POST 在 ApiLoggingMiddleware
写日志时 DataError，整个请求 500——敏感操作二次验证
（POST /api/mfa/confirm）链路全断。

注：日志行由 process_response 单条 INSERT 落库（占位行方案已收口，
事务边界结论见 oplog_recorder.insert_operation_log），字段一次装配完整，
本文件断言可直接校验 method / path 等响应阶段字段。
"""

import pytest

from audit.models import OperationLog

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
    """POST /api/mfa/confirm 不再因写日志 500，日志占位行正常生成。"""
    # 解绑 OTP 的 412 要求 confirm_type=password，密码方式在该级别可验证
    resp = auth_client.post(
        MFA_CONFIRM_URL,
        {"confirm_type": "password", "method": "password", "code": "Admin@123456"},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.data["code"] == 1000

    # 二次验证必须留痕（响应阶段单条 INSERT，行字段完整）。module 取 handler
    # docstring 首行（create 的方法文档）——旧实现在 pytest 事务内 on_commit
    # 不回填，只能见到占位行的类文档值「敏感操作二次验证」，与生产终值不同。
    log = OperationLog.objects.filter(module="提交验证：校验通过后写入确认状态").first()
    assert log is not None
    assert len(log.module) <= 64
    assert log.method == "POST"
    assert log.path == MFA_CONFIRM_URL
    assert log.response_code == 200


def test_confirm_body_password_desensitized(rf, superuser):
    """二次验证提交体里的 code 字段是明文登录密码，落日志前必须掩码。"""
    import json

    from common.core.middleware import build_operation_log_info

    request = rf.post(MFA_CONFIRM_URL, data={})
    request.user = superuser
    request.request_data = {
        "confirm_type": "password",
        "method": "password",
        "code": "Admin@123456",
    }
    request.request_ip = "127.0.0.1"

    class FakeResponse:
        status_code = 200
        renderer_context = {}
        data = {"code": 1000, "detail": "ok"}

    info = build_operation_log_info(request, FakeResponse(), 0.0)
    assert "Admin@123456" not in info["body"]
    assert "************" in info["body"]
    assert json.loads(info["body"])["method"] == "password"


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
    assert log.method == "POST"
    assert log.path == MENU_URL
    assert log.response_code == 200
