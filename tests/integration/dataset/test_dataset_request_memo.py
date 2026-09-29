# -*- coding: utf-8 -*-
"""数据集执行请求级 memo：白名单/字段权限同请求内只查一次（多卡片同屏不放大）。"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from dataset.models import Dataset
from dataset.utils.dataset import available_fields, available_models, execute_dataset, viewer_visible_fields
from server.utils import set_current_request
from system.models import ModelLabelField

pytestmark = pytest.mark.django_db


@pytest.fixture
def request_ctx():
    from types import SimpleNamespace

    # 尽量贴近真实请求对象：部分链路会读 .user/.request_uuid（如数据权限过滤）
    request = SimpleNamespace(user=None, request_uuid="test-request", method="GET", path="/")
    set_current_request(request)
    yield request
    set_current_request(None)


@pytest.fixture
def model_registry(db):
    root, _ = ModelLabelField.objects.get_or_create(
        name="system.userinfo",
        defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "用户"},
    )
    for name in ("username", "nickname"):
        ModelLabelField.objects.get_or_create(
            name=name,
            parent=root,
            defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": name},
        )
    return root


@pytest.fixture
def dataset(model_registry, superuser):
    return Dataset.objects.create(
        name="memo数据集",
        bound_model="system.userinfo",
        columns=["username", "nickname"],
        filters=[],
        row_limit=100,
        visibility="shared",
        creator=superuser,
    )


def _label_queries(ctx):
    return [q["sql"] for q in ctx.captured_queries if "system_modellabelfield" in q["sql"]]


class TestRequestScopedMemo:
    def test_available_fields_cached_within_request(self, request_ctx, model_registry):
        with CaptureQueriesContext(connection) as first:
            assert "username" in available_fields("system.userinfo")
        with CaptureQueriesContext(connection) as second:
            available_fields("system.userinfo")
        assert len(_label_queries(first)) == 1
        assert _label_queries(second) == [], "同请求内白名单第二次命中 memo"

    def test_available_models_cached_within_request(self, request_ctx, model_registry):
        with CaptureQueriesContext(connection) as first:
            assert "system.userinfo" in available_models()
        with CaptureQueriesContext(connection) as second:
            available_models()
        assert len(_label_queries(first)) == 1
        assert _label_queries(second) == []

    def test_new_request_queries_again(self, model_registry):
        from types import SimpleNamespace

        for _ in range(2):
            set_current_request(SimpleNamespace())
            with CaptureQueriesContext(connection) as ctx:
                available_fields("system.userinfo")
            assert len(_label_queries(ctx)) == 1, "memo 按请求隔离，跨请求必须重查"
        set_current_request(None)

    def test_without_request_context_no_memo(self, model_registry):
        """无请求上下文（Celery/命令/直调）不缓存，行为与改造前一致。"""
        set_current_request(None)
        for _ in range(2):
            with CaptureQueriesContext(connection) as ctx:
                available_fields("system.userinfo")
            assert len(_label_queries(ctx)) == 1

    def test_viewer_visible_fields_cached_within_request(self, request_ctx, normal_user, model_registry):
        """字段权限（含 None 结果）同请求只查一次。"""
        with CaptureQueriesContext(connection) as first:
            assert viewer_visible_fields("system.userinfo", normal_user) is None
        with CaptureQueriesContext(connection) as second:
            assert viewer_visible_fields("system.userinfo", normal_user) is None
        assert first.captured_queries, "首次需要查角色/字段权限"
        assert second.captured_queries == [], "第二次（含 None 结果）命中 memo"

    def test_execute_dataset_reuses_whitelist_across_cards(self, dataset, superuser, request_ctx):
        """同请求第二次执行（第二张卡片）不再查白名单（模型白名单 + 字段白名单全命中 memo）。"""
        with CaptureQueriesContext(connection) as first:
            result = execute_dataset(dataset, superuser)
        assert result["columns"] == ["username", "nickname"]
        assert len(_label_queries(first)) == 2, "首次：模型白名单 + 字段白名单各一次"
        with CaptureQueriesContext(connection) as second:
            execute_dataset(dataset, superuser)
        assert _label_queries(second) == [], "同请求第二次执行零白名单查询"
        # 超管不裁剪字段权限：不查字段权限表
        assert not [q["sql"] for q in first.captured_queries if "system_fieldpermission" in q["sql"]]
