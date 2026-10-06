# -*- coding: utf-8 -*-
"""通用标签中心后端单测：白名单 + 打标读写 + 过滤 + 删除保护。

打标权限回落业务对象 update 权限点；非白名单对象 fail-closed；
`?tag=` 多值 AND 语义；删除被引用的标签被拒（保护先解绑）。
"""

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError

from identity.models import UserInfo
from system.models.tag import TAGGABLE_MODELS, Tag, TaggedItem
from system.utils.platform.tags import (
    ensure_tag_permission,
    filter_by_tag_name,
    filter_by_tags,
    object_tags,
    resource_key,
    set_object_tags,
    set_object_tags_batch,
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
        assert "identity.userinfo" in resources

    def test_non_whitelisted_model_rejected(self):
        assert taggable_model("system.role") is None
        assert taggable_model("") is None

    def test_resource_key_matches_whitelist(self):
        assert resource_key(UserInfo) in TAGGABLE_MODELS

    def test_permission_fail_closed_on_non_taggable(self, superuser):
        from identity.models.department import DeptInfo

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


class TestBatchTagging:
    """多对象批量打标：三种模式语义 + 重复执行幂等 + 单对象失败隔离与统计口径。"""

    def test_batch_modes_multi_objects_and_idempotent(self, superuser):
        alice = UserInfo.objects.create(username="tag-batch-a", nickname="A")
        bob = UserInfo.objects.create(username="tag-batch-b", nickname="B")
        vip = Tag.objects.create(name="VIP")
        outsource = Tag.objects.create(name="外包")
        pks = [str(alice.pk), str(bob.pk)]

        # add：入参重复的标签按集合口径合并，逐对象落库
        changed, failed = set_object_tags_batch(UserInfo, pks, [str(vip.pk), str(vip.pk)], mode="add", user=superuser)
        assert not failed and [item["pk"] for item in changed] == pks
        assert [item["name"] for item in changed[0]["tags"]] == ["VIP"]
        assert TaggedItem.objects.count() == 2

        # 重复执行幂等：不产生重复关联，回显不变
        changed_again, failed_again = set_object_tags_batch(UserInfo, pks, [str(vip.pk)], mode="add", user=superuser)
        assert not failed_again
        assert changed_again == changed
        assert TaggedItem.objects.count() == 2

        # remove：只剔除命中标签，其余关联原样保留
        set_object_tags_batch(UserInfo, [str(bob.pk)], [str(outsource.pk)], mode="add", user=superuser)
        changed, failed = set_object_tags_batch(UserInfo, pks, [str(vip.pk)], mode="remove", user=superuser)
        assert not failed
        assert TaggedItem.objects.count() == 1  # 仅 bob 剩「外包」
        assert [item["name"] for item in changed[0]["tags"]] == []
        assert [item["name"] for item in changed[1]["tags"]] == ["外包"]
        # remove 幂等：重复剔除不增不减
        changed, failed = set_object_tags_batch(UserInfo, pks, [str(vip.pk)], mode="remove", user=superuser)
        assert not failed and TaggedItem.objects.count() == 1

        # replace：全量替换（此前为空的对象也写入）
        changed, failed = set_object_tags_batch(UserInfo, pks, [str(vip.pk)], mode="replace", user=superuser)
        assert not failed
        assert TaggedItem.objects.count() == 2
        assert all([item["name"] for item in entry["tags"]] == ["VIP"] for entry in changed)

    def test_batch_multi_tag_order_and_duplicate_targets(self, superuser):
        user = UserInfo.objects.create(username="tag-batch-g", nickname="G")
        zeta = Tag.objects.create(name="zeta")
        alpha = Tag.objects.create(name="alpha")
        mid = Tag.objects.create(name="mid")
        duplicated_pks = [str(user.pk), str(user.pk)]

        # 回显按标签名排序（与单对象打标同序），与入参顺序无关
        changed, failed = set_object_tags_batch(
            UserInfo, duplicated_pks, [str(zeta.pk), str(mid.pk), str(alpha.pk)], mode="replace", user=superuser
        )
        assert not failed
        assert [item["name"] for item in changed[0]["tags"]] == ["alpha", "mid", "zeta"]

        # 入参目标重复：与逐对象循环同口径——每个目标各回显一条，落库不产生重复关联
        assert [item["pk"] for item in changed] == duplicated_pks
        assert TaggedItem.objects.filter(object_id=str(user.pk)).count() == 3

    def test_batch_failure_isolation_and_stats(self, superuser):
        alice = UserInfo.objects.create(username="tag-batch-c", nickname="C")
        bob = UserInfo.objects.create(username="tag-batch-d", nickname="D")
        vip = Tag.objects.create(name="VIP")
        ghost = "00000000-0000-0000-0000-000000000000"

        # 标签不存在：replace 语义下入参即目标集，全部对象失败、不入库
        changed, failed = set_object_tags_batch(
            UserInfo, [str(alice.pk), str(bob.pk)], [str(vip.pk), ghost], mode="replace", user=superuser
        )
        assert not changed
        assert [item["pk"] for item in failed] == [str(alice.pk), str(bob.pk)]
        assert all(item["reason"] for item in failed)
        assert TaggedItem.objects.count() == 0

        # add 模式超上限：现有关联多的对象被拦，其余对象照常成功（失败对象原关联不动）
        hoarded = [Tag.objects.create(name=f"存量-{i}") for i in range(19)]
        set_object_tags(UserInfo, alice.pk, [tag.pk for tag in hoarded], superuser)
        extras = [Tag.objects.create(name=f"新增-{i}") for i in range(2)]
        changed, failed = set_object_tags_batch(
            UserInfo, [str(alice.pk), str(bob.pk)], [tag.pk for tag in extras], mode="add", user=superuser
        )
        assert [item["pk"] for item in changed] == [str(bob.pk)]
        assert [item["pk"] for item in failed] == [str(alice.pk)]
        assert TaggedItem.objects.filter(object_id=str(bob.pk)).count() == 2
        assert TaggedItem.objects.filter(object_id=str(alice.pk)).count() == 19

        # 权限回落拒绝（guard 抛错）：按对象隔离，成功对象照常写库
        carol = UserInfo.objects.create(username="tag-batch-e", nickname="E")
        dave = UserInfo.objects.create(username="tag-batch-f", nickname="F")

        def deny_dave(pk):
            if pk == str(dave.pk):
                raise DjangoValidationError("denied")

        changed, failed = set_object_tags_batch(
            UserInfo, [str(carol.pk), str(dave.pk)], [str(vip.pk)], mode="replace", user=superuser, guard=deny_dave
        )
        assert [item["pk"] for item in changed] == [str(carol.pk)]
        assert [item["name"] for item in changed[0]["tags"]] == ["VIP"]
        assert [item["pk"] for item in failed] == [str(dave.pk)]
        assert not TaggedItem.objects.filter(object_id=str(dave.pk)).exists()


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
