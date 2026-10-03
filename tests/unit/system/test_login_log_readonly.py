# -*- coding: utf-8 -*-
"""登录日志只读收口：删除端点下线 + 权限点退役 + 种子同步。

与操作日志只读化（test_operation_log_enhance.TestAuditLogReadOnly）同口径：
登录成功记录可经 API 抹除即等于审计灭迹通道，强退（会话管理）保留。
"""

import json
from pathlib import Path

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from system.models.log import UserLoginLog

pytestmark = pytest.mark.django_db

LIST_URL = "/api/system/logs/login"
RETIRED_PERMISSION_NAMES = ("destroy:SystemUserLoginLog", "batchDestroy:SystemUserLoginLog")


def _make_log(**kwargs):
    defaults = dict(status=True, ipaddress="127.0.0.1")
    defaults.update(kwargs)
    return UserLoginLog.objects.create(**defaults)


class TestLoginLogReadOnly:
    def test_list_still_available(self, superuser):
        log = _make_log()
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=superuser)
        assert client.get(LIST_URL).status_code == 200
        assert UserLoginLog.objects.filter(pk=log.pk).exists()

    def test_delete_endpoints_removed(self, superuser):
        log = _make_log()
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=superuser)
        assert client.delete(f"{LIST_URL}/{log.pk}").status_code in (404, 405)
        assert client.post(f"{LIST_URL}/batch-destroy", {"pks": [str(log.pk)]}, format="json").status_code in (
            404,
            405,
        )
        assert UserLoginLog.objects.filter(pk=log.pk).exists()

    def test_logout_action_preserved(self, superuser):
        """强退是会话管理动作（非删除），只读化后保留。"""
        # creator / channel_name 为空的行不可强退：走可读错误分支（不触通道层）
        log = _make_log()
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=superuser)
        resp = client.post(f"{LIST_URL}/{log.pk}/logout")
        assert resp.status_code == 200
        assert resp.data["code"] == 400

    def test_seed_no_longer_registers_delete_permission_points(self):
        """种子库（menu.json）不再登记两条删除权限点：新装库与存量库迁移后口径一致。"""
        menu_json = Path(settings.PROJECT_DIR) / "loadjson" / "menu.json"
        names = [entry["fields"]["name"] for entry in json.loads(menu_json.read_text())]
        for name in RETIRED_PERMISSION_NAMES:
            assert name not in names
