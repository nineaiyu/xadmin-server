# -*- coding: utf-8 -*-
"""核心列表接口 SQL 计数基线（N+1 回归「提交即拦」）。

口径：

- **只拦硬上限**：每个端点的计数不得超过「实测基线 + 余量」；趋势看报告不改门禁
  （数据量波动不误报，见方案 §六 R6）；
- 首请求常数开销（字典/配置缓存冷启动）先预热掉，断言与运行方式无关
  （与 tests/unit/common/test_modelset_optimize.py 同一手法）；
- 基线表随「有意增加查询」的功能变更同步上调，并在变更说明中写清原因。

用法：``python -m pytest tests/unit/common/test_sql_baseline.py -q``；
需要重新采集基线时把 ``BASELINE`` 置空并跑一次，控制台会打印实测值。
"""

import pytest
from django.contrib.auth.hashers import make_password
from django.db import connection
from django.test.utils import CaptureQueriesContext

from system.models import UserInfo, UserLoginLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def user_page(db, dept, role, superuser):
    """一页业务数据：用户列表有真实行（N+1 只在多行时暴露，空列表测不出问题）。"""
    superuser.dept = dept
    superuser.save(update_fields=["dept"])
    superuser.roles.add(role)
    for index in range(5):
        user = UserInfo.objects.create(
            username=f"sqlbase{index:02d}",
            nickname=f"基线用户{index:02d}",
            password=make_password("Xadmin@123456"),
            dept=dept,
        )
        user.roles.add(role)
        if index < 3:
            for seq in range(2):
                UserLoginLog.objects.create(
                    creator=user,
                    ipaddress="127.0.0.1",
                    channel_name=f"channel-{user.username}-{seq}",
                    login_type=UserLoginLog.LoginTypeChoices.WEBSOCKET,
                )


# 端点 → (实测基线, 余量)。余量给「配置/字典冷读」等固定差异，不掩盖 N+1（N+1 随行数放大）
BASELINE = {
    # 2026-09-22 实测（含标签预取）：6 用户一页 7 条（dept JOIN + roles/rules/tags 三次批量）
    "/api/system/user": (7, 5),
    "/api/system/role": (4, 5),
    "/api/system/dept": (6, 5),
    "/api/system/logs/operation": (3, 5),
    "/api/system/logs/login": (4, 5),
    "/api/system/exports": (3, 5),
    "/api/system/imports": (3, 5),
    "/api/system/tasks/executions": (3, 5),
    "/api/system/file": (3, 5),
    "/api/system/approvals": (3, 5),
}


def _count(auth_client, url: str, size: int = 15) -> int:
    with CaptureQueriesContext(connection) as ctx:
        response = auth_client.get(url, {"page": 1, "size": size})
    assert response.status_code == 200, f"{url} -> {response.status_code}"
    return len(ctx.captured_queries)


class TestListSqlBaseline:
    def test_measured(self, auth_client, user_page):
        """采集实测值（打印后写入 BASELINE；基线补齐后本用例只是重复采集）。"""
        measured = {}
        for url in BASELINE:
            auth_client.get(url, {"page": 1, "size": 15})  # 预热常数开销
            measured[url] = _count(auth_client, url)
        print("\nSQL baseline:", measured)  # noqa: T201 采集输出（基线维护用）
        for url, (base, margin) in BASELINE.items():
            if base:
                assert measured[url] <= base + margin, (
                    f"{url} SQL 计数 {measured[url]} 超过基线 {base}+{margin}（疑似 N+1 回归）"
                )
