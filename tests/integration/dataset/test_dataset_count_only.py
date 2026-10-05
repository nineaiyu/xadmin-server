# -*- coding: utf-8 -*-
"""数据集 execute 的 count_only 模式。

number/metric 卡全量物化 ≤row_limit 行 × 全列只为读 total：挂屏 M 张数字卡
每刷新周期 = M 次全量行查询。``count_only=True`` 跳过列展开与行物化仅 count，
字段权限与行权限口径与全量路径一致：
- 行权限 fail-closed（无授权 → none()）→ total 恒 0，不泄露行数；
- 字段权限收敛输出列不影响 total（字段白名单管的是"看什么列"）；
- 大屏数字卡链路（_execute_card）已默认走 count_only。
"""

import uuid

import pytest
from django.utils import timezone

from dataset.models.dataset import Dashboard, Dataset, Screen
from dataset.screen_data import _execute_card, collect_screen_cards
from dataset.utils.dataset import execute_dataset
from identity.models import UserInfo
from system.models import DataPermission, FieldPermission, ModelLabelField

pytestmark = pytest.mark.django_db

DATASET_URL = "/api/dataset/datasets"

# 行级数据权限：全部数据（无授权用户行集为 none()，见 test_dashboard_card_permission 同款）
DATA_PERMISSION_ALL_RULES = [
    {"table": "identity.userinfo", "field": "id", "type": "value.all", "match": "all", "value": "", "exclude": False}
]


@pytest.fixture
def model_registry(db):
    root, _ = ModelLabelField.objects.get_or_create(
        name="identity.userinfo",
        defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "用户"},
    )
    for name in ("username", "nickname", "created_time"):
        ModelLabelField.objects.get_or_create(
            name=name, parent=root, defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": name}
        )
    return root


@pytest.fixture
def dataset(model_registry, superuser):
    return Dataset.objects.create(
        name=f"数字卡数据集-{timezone.now().timestamp()}",
        bound_model="identity.userinfo",
        columns=["username", "nickname", "created_time"],
        filters=[],
        row_limit=1000,
        visibility="shared",
        creator=superuser,
    )


def _seed_users(count=3):
    return [UserInfo.objects.create_user(username=f"count_target_{i}", password="Test@123456") for i in range(count)]


class TestExecuteDatasetCountOnly:
    def test_count_only_skips_row_materialization(self, dataset, superuser):
        """count_only：只回 total，不物化 rows / columns。"""
        _seed_users()
        result = execute_dataset(dataset, superuser, count_only=True)
        assert result["rows"] == []
        assert result["columns"] == []
        assert result["total"] == UserInfo.objects.count()
        assert result["limit"] == 1000

    def test_full_mode_still_materializes(self, dataset, superuser):
        """对照：全量模式行为不变（列 + 行 + total）。"""
        _seed_users(2)
        result = execute_dataset(dataset, superuser)
        assert result["columns"] == ["username", "nickname", "created_time"]
        assert len(result["rows"]) == UserInfo.objects.count()
        assert result["total"] == len(result["rows"])

    def test_count_only_respects_row_permission_fail_closed(self, dataset, normal_user):
        """行权限 fail-closed：无授权用户 count_only 的 total 恒 0（不泄露行数）。"""
        _seed_users()
        result = execute_dataset(dataset, normal_user, count_only=True)
        assert result["total"] == 0
        # 对照：授予行权限后 count 恢复真实行数（字段权限不裁 total）
        dp = DataPermission.objects.create(name="全部用户数据-数字卡", rules=DATA_PERMISSION_ALL_RULES)
        normal_user.rules.add(dp)
        result = execute_dataset(dataset, normal_user, count_only=True)
        assert result["total"] == UserInfo.objects.count()

    def test_count_only_field_whitelist_does_not_leak_rows(self, dataset, normal_user, menu_factory):
        """字段白名单（含空集显式配置语义由 viewer_visible_fields 收敛为全量）不改变 count 语义。"""
        _seed_users(2)
        dp = DataPermission.objects.create(name="全部用户数据-字段", rules=DATA_PERMISSION_ALL_RULES)
        normal_user.rules.add(dp)
        menu = menu_factory(name="count-fp-menu", path="api/system/user$", method="GET")
        parent = ModelLabelField.objects.create(
            name="identity.userinfo", label="identity.userinfo", field_type=ModelLabelField.FieldChoices.ROLE
        )
        child = ModelLabelField.objects.create(
            name="username", label="username", parent=parent, field_type=ModelLabelField.FieldChoices.ROLE
        )
        fp = FieldPermission.objects.create(role=normal_user.roles.first(), menu=menu)
        fp.field.add(child)
        result = execute_dataset(dataset, normal_user, count_only=True)
        assert result["total"] == UserInfo.objects.count()
        assert result["rows"] == []  # 白名单列也不物化
        # 对照：全量模式下列被裁剪到白名单
        full = execute_dataset(dataset, normal_user)
        assert full["columns"] == ["username"]


class TestScreenNumberCardUsesCountOnly:
    def test_number_card_executes_count_only(self, dataset, superuser, monkeypatch):
        """大屏 number 卡（kind=execute）链路默认 count_only：M 卡刷新不再全量物化。"""
        _seed_users()
        calls = []
        real_execute = execute_dataset

        def spy_execute(ds, user, count_only=False):
            calls.append(count_only)
            return real_execute(ds, user, count_only=count_only)

        monkeypatch.setattr("dataset.utils.dataset_query.execute_dataset", spy_execute)
        dashboard = Dashboard.objects.create(
            name="数字卡看板",
            layout=[{"id": "c1", "dataset": str(dataset.pk), "title": "总数", "chart_type": "number"}],
            visibility="shared",
            creator=superuser,
        )
        screen = Screen.objects.create(
            name=f"数字卡大屏-{uuid.uuid4().hex[:8]}",
            dashboards=[str(dashboard.pk)],
            layout=[],
            visibility="shared",
            creator=superuser,
        )
        ref = collect_screen_cards(screen)[0]
        assert ref["kind"] == "execute"
        result = _execute_card(ref, superuser)
        assert calls == [True]
        assert result["total"] == UserInfo.objects.count()
        assert result["rows"] == []
