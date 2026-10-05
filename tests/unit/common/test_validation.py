# -*- coding: utf-8 -*-
"""通用校验工具（common/core/validation.py）单元测试。

覆盖「活跃唯一」mixin 的语义（重复拒绝 / 自身排除 / 软删不占用）与必填修剪
helper；并用源码级断言钉住岗位 / 角色 / 菜单三处序列化器复用同一实现
（防回退为平行实现）。
"""

from pathlib import Path

import pytest
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.core.validation import ActiveUniqueValidationMixin, trim_required
from identity.models import Post
from identity.serializers.post import PostSerializer
from identity.serializers.role import RoleSerializer
from system.serializers.menu import MenuSerializer

pytestmark = pytest.mark.django_db

SERVER_ROOT = Path(__file__).resolve().parents[3]


class _HostSerializer(ActiveUniqueValidationMixin, serializers.Serializer):
    """最小宿主：为 mixin 提供 Meta.model / instance（模拟 ModelSerializer 契约）。"""

    class Meta:
        model = Post


class TestTrimRequired:
    def test_strips_and_returns(self):
        assert trim_required("  abc  ", "x is required") == "abc"

    def test_rejects_none_and_blank(self):
        for value in (None, "", "   "):
            with pytest.raises(ValidationError):
                trim_required(value, "x is required")


class TestActiveUniqueMixin:
    def test_rejects_duplicate_name(self):
        Post.objects.create(name="岗位A", code="post_a")
        with pytest.raises(ValidationError):
            _HostSerializer()._validate_active_unique("name", "岗位A")

    def test_excludes_self_on_update(self):
        post = Post.objects.create(name="岗位A", code="post_a")
        host = _HostSerializer(instance=post)
        assert host._validate_active_unique("name", "岗位A") == "岗位A"

    def test_soft_deleted_row_does_not_occupy_name(self):
        post = Post.objects.create(name="岗位A", code="post_a")
        post.delete()  # 软删除：默认管理器不再返回该行
        assert _HostSerializer()._validate_active_unique("name", "岗位A") == "岗位A"


class TestSerializerIntegration:
    def test_post_serializer_reports_duplicate_as_field_error(self):
        """重复名走字段级 400（而非数据库 IntegrityError）。"""
        Post.objects.create(name="岗位A", code="post_a")
        serializer = PostSerializer(data={"name": "岗位A", "code": "post_b"})
        assert not serializer.is_valid()
        assert "name" in serializer.errors


class TestSharedImplementation:
    """岗位 / 角色 / 菜单复用同一 mixin（防回退为平行实现）。"""

    def test_serializers_inherit_mixin(self):
        for serializer_cls in (PostSerializer, RoleSerializer, MenuSerializer):
            assert issubclass(serializer_cls, ActiveUniqueValidationMixin)

    def test_no_parallel_implementations_remain(self):
        for relative in ("identity/serializers/post.py", "identity/serializers/role.py"):
            source = (SERVER_ROOT / relative).read_text(encoding="utf-8")
            assert "def _validate_active_unique" not in source
        menu_source = (SERVER_ROOT / "system/serializers/menu.py").read_text(encoding="utf-8")
        assert "Menu.objects.filter(name=value)" not in menu_source
