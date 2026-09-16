# -*- coding: utf-8 -*-
"""AI 观测看板（B1）集成测试：用量 / 成功率 / 日趋势 / 类型分布 / Top 用户聚合。

数据源 = OperationLog(auth_type=ai)；权限点与 status 共用 `(status|metrics)$` 路径正则。
断言全部采用「基线差值」形态：前序用例可能以非回滚事务/后台线程写入 AI 审计，
绝对计数不可靠（本文件曾因此偶发失败）。
"""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from system.models import Menu, OperationLog, UserInfo

pytestmark = pytest.mark.django_db

METRICS_URL = "/api/system/ai/assistant/metrics"


def seed_ai_log(module, ok, user, days_ago=0):
    log = OperationLog.objects.create(
        module=module,
        object_pk=str(user.pk),
        auth_type=OperationLog.AuthType.AI,
        status_code=1000 if ok else 1001,
        response_code=1000 if ok else 1001,
        changes=json.dumps({"status": "ok" if ok else "failed"}, ensure_ascii=False),
    )
    if days_ago:
        OperationLog.objects.filter(pk=log.pk).update(created_time=timezone.now() - timedelta(days=days_ago))
    return log


@pytest.fixture
def metrics_user(db, role, menu_factory):
    """持有 AI 助手 status 权限点（路径正则同时覆盖 metrics）的用户。"""
    user = UserInfo.objects.create_user(username="ai_metric_admin", password="Test@123456", nickname="观测量")
    user.roles.add(role)
    perm = Menu.objects.filter(name="status:AiAssistant").first() or menu_factory(
        "status:AiAssistant", path="api/system/ai/assistant/(status|metrics)$", method="GET"
    )
    role.menu.add(perm)
    return user


@pytest.fixture
def metrics_client(api_client, metrics_user):
    api_client.force_authenticate(user=metrics_user)
    return api_client


class TestAiMetrics:
    @staticmethod
    def _query(client, days=7):
        """返回 (data, {module: count})——调用方的断言只依赖两次调用之间的差值。"""
        data = client.get(METRICS_URL, {"days": days}).json()["data"]
        return data, {row["module"]: row["count"] for row in data["by_module"]}

    def test_aggregation_across_modules_and_days(self, metrics_client, metrics_user):
        other = UserInfo.objects.create_user(username="ai_other", password="Test@123456", nickname="他人")
        base, base_modules = self._query(metrics_client)
        base_ask = base_modules.get("AI:ask", 0)
        base_nl = base_modules.get("AI:nl_query", 0)

        seed_ai_log("AI:ask", True, metrics_user)
        seed_ai_log("AI:ask", False, metrics_user)
        seed_ai_log("AI:nl_query", True, other, days_ago=1)
        seed_ai_log("AI:action", True, other, days_ago=40)  # 7 天窗口外

        data, modules = self._query(metrics_client)
        assert data["days"] == 7
        assert data["total"] == base["total"] + 3
        assert modules.get("AI:ask", 0) == base_ask + 2
        assert modules.get("AI:nl_query", 0) == base_nl + 1

        labels = {row["module"]: row["label"] for row in data["by_module"]}
        assert labels["AI:ask"] == "文档问答"

        top = {row["username"]: row["count"] for row in data["top_users"]}
        assert top.get("ai_metric_admin", 0) >= 2
        assert top.get("ai_other", 0) >= 1

        dates = {row["date"] for row in data["by_day"]}
        assert len(dates) >= 2  # 本次 seed 覆盖今天与昨天

        wide, _ = self._query(metrics_client, days=70)
        assert wide["total"] == data["total"] + 1  # 40 天前的记录只计入宽窗口

    def test_days_param_clamped(self, metrics_client):
        assert metrics_client.get(METRICS_URL, {"days": "999"}).json()["data"]["days"] == 90
        assert metrics_client.get(METRICS_URL, {"days": "0"}).json()["data"]["days"] == 1
        assert metrics_client.get(METRICS_URL, {"days": "abc"}).json()["data"]["days"] == 30

    def test_narrow_window_excludes_old_logs(self, metrics_client, metrics_user):
        seed_ai_log("AI:ask", True, metrics_user, days_ago=60)
        narrow, _ = self._query(metrics_client, days=1)
        wide, _ = self._query(metrics_client, days=70)
        assert wide["total"] - narrow["total"] >= 1

    def test_non_ai_audit_not_counted(self, metrics_client, metrics_user):
        # 不变量断言（抗并发污染）：auth_type=ai 过滤生效——非 AI 审计永不进入分布
        OperationLog.objects.create(
            module="login",
            object_pk=str(metrics_user.pk),
            auth_type=OperationLog.AuthType.JWT,
            status_code=1000,
            response_code=1000,
        )
        data, _ = self._query(metrics_client)
        assert all(row["module"] != "login" for row in data["by_module"])
        assert all((row["module"] or "").startswith("AI:") for row in data["by_module"])
