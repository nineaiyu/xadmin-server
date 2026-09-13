# -*- coding: utf-8 -*-
"""字段级审计 diff 集成测试（白名单模型的 update 写入 OperationLog.changes）。"""

import json

import pytest

from notifications.models import MessageContent
from system.models import OperationLog

pytestmark = pytest.mark.django_db

NOTICE_URL = "/api/notifications/notice-messages"


@pytest.fixture
def notice(db, superuser):
    return MessageContent.objects.create(title="审计测试", message="<p>v1</p>", notice_type=2)


def test_update_records_changes(auth_client, notice, monkeypatch, django_capture_on_commit_callbacks):
    # SysConfig 有存储级缓存（settings 回退值只在首读时生效），
    # 单测内扩容白名单走 patch property（与 test_export_record 的 SysConfig 覆写范式一致）
    from common.core.config import SysConfig

    monkeypatch.setattr(
        type(SysConfig), "AUDIT_DIFF_MODELS", property(lambda self: ["notifications.MessageContent"]), raising=False
    )
    with django_capture_on_commit_callbacks(execute=True):
        resp = auth_client.patch(f"{NOTICE_URL}/{notice.pk}", {"title": "审计测试v2"}, format="json")
    assert resp.status_code == 200, resp.data

    log = OperationLog.objects.filter(path__icontains="notice-messages", method="PATCH").latest("id")
    changes = json.loads(log.changes)
    assert changes["title"]["old"] == "审计测试"
    assert changes["title"]["new"] == "审计测试v2"
    # 中间件从 detail 路由 URL kwargs 提取对象主键（转 str 存储）
    assert log.object_pk == str(notice.pk)


def test_non_whitelisted_model_not_recorded(auth_client, notice, django_capture_on_commit_callbacks):
    """非白名单模型（MessageContent 不在默认清单内）：不记录 diff，也不额外查询。"""
    with django_capture_on_commit_callbacks(execute=True):
        resp = auth_client.patch(f"{NOTICE_URL}/{notice.pk}", {"title": "审计测试v3"}, format="json")
    assert resp.status_code == 200, resp.data

    log = OperationLog.objects.filter(path__icontains="notice-messages", method="PATCH").latest("id")
    assert not log.changes


def test_default_whitelist_covers_user_management(settings):
    """默认白名单覆盖「用户管理」：它是当前唯一挂「变更历史」入口的页面。

    回归守护：默认清单曾被置空 → 变更历史弹窗的字段明细恒为空（前端显示「—」），
    用户改了性别/昵称也看不到任何 diff。
    """
    assert "system.UserInfo" in (settings.AUDIT_DIFF_MODELS or [])


def test_user_update_records_changes_by_default(
    auth_client, superuser, normal_user, django_capture_on_commit_callbacks
):
    """默认白名单下的端到端：用户管理改性别/昵称即落 changes（变更明细可见）。"""
    with django_capture_on_commit_callbacks(execute=True):
        resp = auth_client.patch(f"/api/system/user/{normal_user.pk}", {"gender": 2, "nickname": "改名"}, format="json")
    assert resp.status_code == 200, resp.data

    log = OperationLog.objects.filter(path=f"/api/system/user/{normal_user.pk}", method="PATCH").latest("id")
    changes = json.loads(log.changes)
    assert changes["gender"]["old"] == "0"
    assert changes["gender"]["new"] == "2"
    assert changes["nickname"]["new"] == "改名"


def test_list_request_has_no_object_pk(auth_client, django_capture_on_commit_callbacks):
    """计划 Task 2.3：list/create 等无 pk 路由的日志行 object_pk 留空（仅 detail 路由提取）。

    GET 不在 API_LOG_METHODS 白名单，用 POST create（同样无 pk kwargs）验证。
    """
    with django_capture_on_commit_callbacks(execute=True):
        resp = auth_client.post("/api/system/dict", {"code": "oplog-pk-none", "label": "对象定位"}, format="json")
    assert resp.status_code == 200, resp.data

    log = OperationLog.objects.filter(path="/api/system/dict", method="POST").latest("id")
    assert not log.object_pk


def test_m2m_changes_recorded(auth_client, superuser, menu_factory, monkeypatch, django_capture_on_commit_callbacks):
    """M2M 关系变更纳入 diff（角色菜单授权）：与标量字段同形态落 changes。"""
    from common.core.config import SysConfig
    from system.models import UserRole

    monkeypatch.setattr(type(SysConfig), "AUDIT_DIFF_MODELS", property(lambda self: ["system.UserRole"]), raising=False)
    role = UserRole.objects.create(name="审计角色", code="audit-diff-m2m")
    menu = menu_factory("授权菜单", path="api/audit-m2m$", method="GET")

    with django_capture_on_commit_callbacks(execute=True):
        resp = auth_client.put(
            f"/api/system/role/{role.pk}",
            {"name": "审计角色v2", "code": "audit-diff-m2m", "menu": [str(menu.pk)], "fields": {}},
            format="json",
        )
    assert resp.status_code == 200, resp.data

    log = OperationLog.objects.filter(path=f"/api/system/role/{role.pk}", method="PUT").latest("id")
    changes = json.loads(log.changes)
    assert changes["name"]["old"] == "审计角色"
    assert changes["name"]["new"] == "审计角色v2"
    assert not changes["menu"]["old"]
    assert changes["menu"]["new"] == str(menu.pk)


def test_m2m_unchanged_not_recorded(
    auth_client, superuser, menu_factory, monkeypatch, django_capture_on_commit_callbacks
):
    """M2M 未变化的 update 不产生该字段 diff（groups 等恒空关系亦无噪声）。"""
    from common.core.config import SysConfig
    from system.models import UserRole

    monkeypatch.setattr(type(SysConfig), "AUDIT_DIFF_MODELS", property(lambda self: ["system.UserRole"]), raising=False)
    role = UserRole.objects.create(name="无变化角色", code="audit-diff-m2m-2")

    with django_capture_on_commit_callbacks(execute=True):
        resp = auth_client.put(
            f"/api/system/role/{role.pk}",
            {"name": "无变化角色v2", "code": "audit-diff-m2m-2", "fields": {}},
            format="json",
        )
    assert resp.status_code == 200, resp.data

    log = OperationLog.objects.filter(path=f"/api/system/role/{role.pk}", method="PUT").latest("id")
    changes = json.loads(log.changes)
    assert "menu" not in changes
    assert changes["name"]["new"] == "无变化角色v2"
