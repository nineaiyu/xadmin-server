# -*- coding: utf-8 -*-
"""common/core/serializers.py 字段权限裁剪单元测试。"""
import pytest
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from demo.serializers.book import BookSerializer
from server.utils import set_current_request
from system.models import FieldPermission, ModelLabelField

pytestmark = pytest.mark.django_db

factory = APIRequestFactory()

ALL_FIELDS = {
    "pk", "block", "name", "isbn", "category", "is_active", "author", "publisher",
    "publication_date", "price", "created_time", "updated_time",
    "admin", "admin2", "managers", "managers2", "avatar", "cover", "book_file", "file", "files",
}


@pytest.fixture
def make_request_ctx():
    """构造已进入 thread-local 的请求上下文（BaseModelSerializer 依赖 get_current_request）。

    force_authenticate 必须在 Request 包装前设置到底层 WSGIRequest。
    """

    def _make(user=None, path="/api/demo/book"):
        django_request = factory.get(path)
        if user is not None:
            force_authenticate(django_request, user=user)
        request = Request(django_request, authenticators=[])
        set_current_request(request)
        return request

    return _make


def make_field_tree():
    """构建 demo.book 模型的字段权限标签树（parent=模型名，child=字段名）。"""
    model_field = ModelLabelField.objects.create(
        name="demo.book", label="书籍", field_type=ModelLabelField.FieldChoices.ROLE
    )
    f1 = ModelLabelField.objects.create(
        name="name", label="书名", parent=model_field, field_type=ModelLabelField.FieldChoices.ROLE
    )
    f2 = ModelLabelField.objects.create(
        name="author", label="作者", parent=model_field, field_type=ModelLabelField.FieldChoices.ROLE
    )
    return model_field, [f1, f2]


class TestGetAllowFields:
    def test_without_request_context_keeps_all_fields(self, db):
        serializer = BookSerializer()
        assert set(serializer.fields.keys()) == ALL_FIELDS

    def test_superuser_sees_all_fields(self, superuser, make_request_ctx):
        make_request_ctx(superuser)
        serializer = BookSerializer()
        assert set(serializer.fields.keys()) == ALL_FIELDS

    def test_fields_restricted_by_request_fields(self, normal_user, make_request_ctx):
        request = make_request_ctx(normal_user)
        request.fields = {"demo.book": {"name", "author", "pk"}}
        serializer = BookSerializer()
        assert set(serializer.fields.keys()) == {"pk", "name", "author"}

    def test_no_field_config_for_model_keeps_nothing(self, normal_user, make_request_ctx):
        request = make_request_ctx(normal_user)
        request.fields = {}
        serializer = BookSerializer()
        assert set(serializer.fields.keys()) == set()

    def test_ignore_field_permission_param_keeps_all(self, normal_user, make_request_ctx):
        request = make_request_ctx(normal_user)
        request.fields = {"demo.book": {"name"}}
        serializer = BookSerializer(ignore_field_permission=True)
        assert set(serializer.fields.keys()) == ALL_FIELDS

    def test_request_ignore_field_permission_flag(self, normal_user, make_request_ctx):
        """权限流程对超管/白名单请求会设置 request.ignore_field_permission。"""
        request = make_request_ctx(normal_user)
        request.fields = {"demo.book": {"name"}}
        request.ignore_field_permission = True
        serializer = BookSerializer()
        assert set(serializer.fields.keys()) == ALL_FIELDS

    def test_field_permission_disabled_keeps_all(self, normal_user, make_request_ctx, settings):
        settings.PERMISSION_FIELD_ENABLED = False
        request = make_request_ctx(normal_user)
        request.fields = {"demo.book": {"name"}}
        serializer = BookSerializer()
        assert set(serializer.fields.keys()) == ALL_FIELDS


class TestFieldPermissionIntegration:
    def test_get_user_field_queryset_via_role(self, normal_user, role, menu_factory, make_request_ctx):
        """角色 -> FieldPermission -> 字段标签 的完整链路。"""
        from common.core.permission import get_user_field_queryset

        menu = menu_factory("p-list", path="api/demo/book$", method="GET")
        role.menu.add(menu)
        model_field, children = make_field_tree()
        fp = FieldPermission.objects.create(role=role, menu=menu)
        fp.field.add(*children)

        data = get_user_field_queryset(normal_user, menu.pk)
        assert data == {"demo.book": {"name", "author"}}

        request = make_request_ctx(normal_user)
        request.fields = data
        serializer = BookSerializer()
        assert set(serializer.fields.keys()) == {"name", "author"}
