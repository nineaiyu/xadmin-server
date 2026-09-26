# -*- coding: utf-8 -*-
"""通用标签中心API 集成测试：标签 CRUD / 打标 / 过滤 / 删除保护 / 白名单。

打标权限回落业务对象 update 权限点（超管天然具备；普通用户被拒）；
列表 `?tag=` 过滤走 TagFilterBackend（AND 语义）；元数据下发 tag 搜索字段。
"""

import pytest

from system.models import UserInfo
from system.models.tag import Tag, TaggedItem

pytestmark = pytest.mark.django_db

TAGS_URL = "/api/system/tags"
USER_TAG_PATH = "api/system/user/(?P<pk>[^/.]+)$"


@pytest.fixture(autouse=True)
def _clean_tags():
    TaggedItem.objects.all().delete()
    Tag.objects.all().delete()
    yield
    TaggedItem.objects.all().delete()
    Tag.objects.all().delete()


class TestTagCrud:
    def test_anonymous_rejected(self, api_client):
        assert api_client.get(TAGS_URL).status_code == 401

    def test_create_and_list_with_usage_count(self, auth_client):
        response = auth_client.post(TAGS_URL, {"name": "重点客户", "color": "#409EFF"}, format="json")
        assert response.status_code == 200, response.data
        pk = response.json()["data"]["pk"]
        listing = auth_client.get(TAGS_URL).json()["data"]["results"]
        assert listing[0]["name"] == "重点客户" and listing[0]["usage_count"] == 0
        assert listing[0]["pk"] == pk

    def test_name_required_and_color_validated(self, auth_client):
        assert auth_client.post(TAGS_URL, {"name": "  "}, format="json").status_code == 400
        assert auth_client.post(TAGS_URL, {"name": "x", "color": "red"}, format="json").status_code == 400

    def test_duplicate_name_rejected(self, auth_client):
        auth_client.post(TAGS_URL, {"name": "重复"}, format="json")
        assert auth_client.post(TAGS_URL, {"name": "重复"}, format="json").status_code == 400

    def test_resources_endpoint(self, auth_client):
        data = auth_client.get(f"{TAGS_URL}/resources").json()["data"]
        assert {item["key"] for item in data["resources"]} == {
            "system.userinfo",
            "system.uploadfile",
            "approval.approvalinstance",
        }


class TestAssign:
    def test_assign_and_read_back(self, auth_client, superuser):
        user = UserInfo.objects.create(username="tag-api-1", nickname="打标用户")
        tag = Tag.objects.create(name="外包")
        response = auth_client.post(
            f"{TAGS_URL}/assign",
            {"resource": "system.userinfo", "pk": str(user.pk), "tags": [str(tag.pk)]},
            format="json",
        )
        assert response.status_code == 200, response.data
        assert [item["name"] for item in response.json()["data"]["tags"]] == ["外包"]
        objects = auth_client.get(f"{TAGS_URL}/objects?resource=system.userinfo&pk={user.pk}").json()["data"]
        assert [item["name"] for item in objects["tags"]] == ["外包"]
        # 业务序列化器回显（列表 tags 字段）
        rows = auth_client.get("/api/system/user?username=tag-api-1").json()["data"]["results"]
        assert [item["name"] for item in rows[0]["tags"]] == ["外包"]

    def test_unknown_resource_rejected(self, auth_client):
        response = auth_client.post(
            f"{TAGS_URL}/assign", {"resource": "system.role", "pk": "1", "tags": []}, format="json"
        )
        assert response.status_code == 400

    def test_unknown_tag_rejected(self, auth_client):
        user = UserInfo.objects.create(username="tag-api-2", nickname="打标用户")
        response = auth_client.post(
            f"{TAGS_URL}/assign",
            {"resource": "system.userinfo", "pk": str(user.pk), "tags": ["00000000-0000-0000-0000-000000000000"]},
            format="json",
        )
        assert response.json()["code"] == 1001

    def test_batch_assign_modes(self, auth_client):
        users = [UserInfo.objects.create(username=f"tag-batch-{i}", nickname=f"批量{i}") for i in range(2)]
        first, second = Tag.objects.create(name="A"), Tag.objects.create(name="B")
        pks = [str(user.pk) for user in users]
        added = auth_client.post(
            f"{TAGS_URL}/batch-assign",
            {"resource": "system.userinfo", "pks": pks, "tags": [str(first.pk)], "mode": "add"},
            format="json",
        ).json()
        assert added["code"] == 1000 and len(added["data"]["success"]) == 2
        removed = auth_client.post(
            f"{TAGS_URL}/batch-assign",
            {"resource": "system.userinfo", "pks": pks, "tags": [str(first.pk)], "mode": "remove"},
            format="json",
        ).json()
        assert removed["code"] == 1000
        assert TaggedItem.objects.count() == 0
        replaced = auth_client.post(
            f"{TAGS_URL}/batch-assign",
            {"resource": "system.userinfo", "pks": pks, "tags": [str(second.pk)], "mode": "replace"},
            format="json",
        ).json()
        assert replaced["code"] == 1000 and TaggedItem.objects.count() == 2

    def test_normal_user_without_object_permission_rejected(self, normal_user):
        """双门：菜单权限点（403）或业务对象 update 权限回落（1001）任一拦截即通过。"""
        user = UserInfo.objects.create(username="tag-api-3", nickname="打标用户")
        from rest_framework.test import APIClient

        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        response = client.post(
            f"{TAGS_URL}/assign", {"resource": "system.userinfo", "pk": str(user.pk), "tags": []}, format="json"
        )
        assert response.status_code == 403 or response.json()["code"] == 1001
        assert not TaggedItem.objects.exists()


class TestFilterAndDeleteProtection:
    def test_list_filter_by_tag_name(self, auth_client):
        tagged = UserInfo.objects.create(username="tag-filter-yes", nickname="筛选")
        other = UserInfo.objects.create(username="tag-filter-no", nickname="筛选")
        tag = Tag.objects.create(name="筛选标签")
        auth_client.post(
            f"{TAGS_URL}/assign",
            {"resource": "system.userinfo", "pk": str(tagged.pk), "tags": [str(tag.pk)]},
            format="json",
        )
        rows = auth_client.get("/api/system/user?tag=筛选标签").json()["data"]["results"]
        usernames = {row["username"] for row in rows}
        assert "tag-filter-yes" in usernames and "tag-filter-no" not in usernames
        assert other.username not in usernames

    def test_search_fields_expose_tag(self, auth_client):
        fields = auth_client.get("/api/system/user/search-fields").json()["data"]
        tag_field = [item for item in fields if item["key"] == "tag"]
        assert tag_field and tag_field[0]["input_type"] == "select"

    def test_filter_accepts_pk_and_multi_tokens(self, auth_client):
        """过滤值口径与过滤器解析一致：标签主键 / 逗号分隔多标签都应通过校验。

        回归（本地容器验收发现）：``tag`` 下拉 choices 只含标签名时，主键取值与多标签组合
        会被 ``ChoiceField`` 校验拦成 400「不在可用的选项中」——深链带主键、多标签 AND 组合、
        以及刚新建标签的 60s 下拉缓存窗口内都会命中；未命中标签按空集收敛（fail-closed）。
        """
        first = UserInfo.objects.create(username="tag-filter-multi-1", nickname="筛选")
        second = UserInfo.objects.create(username="tag-filter-multi-2", nickname="筛选")
        tag_a = Tag.objects.create(name="组合标签A")
        tag_b = Tag.objects.create(name="组合标签B")
        auth_client.post(
            f"{TAGS_URL}/assign",
            {"resource": "system.userinfo", "pk": str(first.pk), "tags": [str(tag_a.pk), str(tag_b.pk)]},
            format="json",
        )
        auth_client.post(
            f"{TAGS_URL}/assign",
            {"resource": "system.userinfo", "pk": str(second.pk), "tags": [str(tag_a.pk)]},
            format="json",
        )

        by_pk = auth_client.get(f"/api/system/user?tag={tag_a.pk}")
        assert by_pk.status_code == 200 and by_pk.json()["code"] == 1000
        names = {row["username"] for row in by_pk.json()["data"]["results"]}
        assert {"tag-filter-multi-1", "tag-filter-multi-2"} <= names

        both = auth_client.get(f"/api/system/user?tag={tag_a.name},{tag_b.name}")
        assert both.status_code == 200 and both.json()["code"] == 1000
        names = {row["username"] for row in both.json()["data"]["results"]}
        assert names == {"tag-filter-multi-1"}  # AND 语义：两个标签都命中才返回

        unknown = auth_client.get("/api/system/user?tag=不存在的标签")
        assert unknown.status_code == 200 and unknown.json()["data"]["total"] == 0

    def test_delete_protection(self, auth_client):
        user = UserInfo.objects.create(username="tag-del-1", nickname="删除保护")
        tag = Tag.objects.create(name="被引用")
        auth_client.post(
            f"{TAGS_URL}/assign",
            {"resource": "system.userinfo", "pk": str(user.pk), "tags": [str(tag.pk)]},
            format="json",
        )
        assert auth_client.delete(f"{TAGS_URL}/{tag.pk}").status_code == 400
        assert Tag.objects.filter(pk=tag.pk).exists()

    def test_delete_unused_tag(self, auth_client):
        tag = Tag.objects.create(name="未引用")
        assert auth_client.delete(f"{TAGS_URL}/{tag.pk}").status_code in (200, 204)
        assert not Tag.objects.filter(pk=tag.pk).exists()
