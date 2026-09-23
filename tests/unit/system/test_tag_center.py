# -*- coding: utf-8 -*-
"""通用标签中心（P-1）后端单测：白名单 + 打标读写 + 过滤 + 删除保护。

打标权限回落业务对象 update 权限点；非白名单对象 fail-closed；
`?tag=` 多值 AND 语义；删除被引用的标签被拒（保护先解绑）。
"""

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError

from system.models import UserInfo
from system.models.tag import TAGGABLE_MODELS, Tag, TaggedItem
from system.utils.tags import (
    ensure_tag_permission,
    filter_by_tag_name,
    filter_by_tags,
    object_tags,
    resource_key,
    set_object_tags,
    taggable_model,
    taggable_resources,
    tags_for_instance,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_tags():
    TaggedItem.objects.all().delete()
    Tag.objects.all().delete()
    yield
    TaggedItem.objects.all().delete()
    Tag.objects.all().delete()


class TestWhitelist:
    def test_taggable_resources(self):
        resources = {item["key"] for item in taggable_resources()}
        assert resources == set(TAGGABLE_MODELS)
        assert "system.userinfo" in resources

    def test_non_whitelisted_model_rejected(self):
        assert taggable_model("system.role") is None
        assert taggable_model("") is None

    def test_resource_key_matches_whitelist(self):
        assert resource_key(UserInfo) in TAGGABLE_MODELS

    def test_permission_fail_closed_on_non_taggable(self, superuser):
        from system.models.department import DeptInfo

        # 非白名单对象：打标权限无从回落 → 直接拒绝（fail-closed）
        with pytest.raises(DjangoValidationError):
            ensure_tag_permission(superuser, DeptInfo, "1")


class TestTagging:
    def test_set_object_tags_replaces(self, superuser):
        user = UserInfo.objects.create(username="tag-demo-1", nickname="标签用户")
        first = Tag.objects.create(name="重点客户")
        second = Tag.objects.create(name="外包")
        assert [item["name"] for item in set_object_tags(UserInfo, user.pk, [first.pk], superuser)] == ["重点客户"]
        replaced = set_object_tags(UserInfo, user.pk, [second.pk], superuser)
        assert [item["name"] for item in replaced] == ["外包"]
        assert TaggedItem.objects.count() == 1

    def test_unknown_tag_rejected(self):
        user = UserInfo.objects.create(username="tag-demo-2", nickname="标签用户")
        with pytest.raises(DjangoValidationError):
            set_object_tags(UserInfo, user.pk, ["00000000-0000-0000-0000-000000000000"])

    def test_too_many_tags_rejected(self, superuser):
        user = UserInfo.objects.create(username="tag-demo-3", nickname="标签用户")
        tags = [Tag.objects.create(name=f"批量{i}") for i in range(21)]
        with pytest.raises(DjangoValidationError):
            set_object_tags(UserInfo, user.pk, [tag.pk for tag in tags], superuser)

    def test_tags_for_instance_and_object_tags(self, superuser):
        user = UserInfo.objects.create(username="tag-demo-4", nickname="标签用户")
        tag = Tag.objects.create(name="合同", color="#409EFF")
        set_object_tags(UserInfo, user.pk, [tag.pk], superuser)
        assert object_tags(UserInfo, user.pk)[0] == {"pk": str(tag.pk), "name": "合同", "color": "#409EFF"}
        assert tags_for_instance(user) == object_tags(UserInfo, user.pk)

    def test_empty_pk_rejected(self):
        with pytest.raises(DjangoValidationError):
            set_object_tags(UserInfo, "", [])


class TestFiltering:
    def test_single_and_multi_tag_and_semantics(self, superuser):
        alice = UserInfo.objects.create(username="tag-alice", nickname="A")
        bob = UserInfo.objects.create(username="tag-bob", nickname="B")
        vip = Tag.objects.create(name="VIP")
        outsource = Tag.objects.create(name="外包")
        set_object_tags(UserInfo, alice.pk, [vip.pk], superuser)
        set_object_tags(UserInfo, bob.pk, [vip.pk, outsource.pk], superuser)

        queryset = UserInfo.objects.filter(username__startswith="tag-")
        assert set(filter_by_tags(queryset, UserInfo, [vip.name]).values_list("username", flat=True)) == {
            "tag-alice",
            "tag-bob",
        }
        assert set(
            filter_by_tags(queryset, UserInfo, [vip.name, outsource.name]).values_list("username", flat=True)
        ) == {"tag-bob"}
        # 未知标签 → 空集（不是全量）
        assert not filter_by_tags(queryset, UserInfo, ["不存在的标签"]).exists()
        # 按主键过滤与按名称等价
        assert set(filter_by_tags(queryset, UserInfo, [str(vip.pk)]).values_list("username", flat=True)) == {
            "tag-alice",
            "tag-bob",
        }

    def test_filter_by_tag_name_helper(self, superuser):
        alice = UserInfo.objects.create(username="tag-carol", nickname="C")
        tag = Tag.objects.create(name="离职待办")
        set_object_tags(UserInfo, alice.pk, [tag.pk], superuser)
        queryset = filter_by_tag_name(UserInfo.objects.all(), "离职待办")
        assert list(queryset.values_list("username", flat=True)) == ["tag-carol"]

    def test_empty_tokens_passthrough(self):
        queryset = UserInfo.objects.all()
        assert filter_by_tags(queryset, UserInfo, []) is queryset
        assert filter_by_tag_name(queryset, "") is queryset
