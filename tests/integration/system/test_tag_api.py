# -*- coding: utf-8 -*-
"""通用标签中心API 集成测试：标签 CRUD / 打标 / 过滤 / 删除保护 / 白名单。

打标权限回落业务对象 update 权限点（超管天然具备；普通用户被拒）；
读口（objects）要求请求者对目标对象可见（各域列表页同源可见域，不可见 404）；
列表 `?tag=` 过滤走 TagFilterBackend（AND 语义）；元数据下发 tag 搜索字段。
"""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache as django_cache

from identity.models import UserInfo
from system.models import DataPermission
from system.models.tag import Tag, TaggedItem

pytestmark = pytest.mark.django_db

TAGS_URL = "/api/system/tags"
USER_TAG_PATH = "api/system/user/(?P<pk>[^/.]+)$"
TAG_OBJECTS_PERMISSION = "api/system/tags/objects$"


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
        # 严格十六进制口径（COLOR_PATTERN）：非 #、位数不足/超长、非法字符都拒绝
        for bad in ("409EFF", "#409EF", "#409EFFF", "#zzzzzz", "#409EFF;"):
            assert auth_client.post(TAGS_URL, {"name": "x", "color": bad}, format="json").status_code == 400, bad
        ok = auth_client.post(TAGS_URL, {"name": "合法色", "color": "#409EFF"}, format="json")
        assert ok.status_code == 200
        assert ok.json()["data"]["color"] == "#409EFF"

    def test_duplicate_name_rejected(self, auth_client):
        auth_client.post(TAGS_URL, {"name": "重复"}, format="json")
        assert auth_client.post(TAGS_URL, {"name": "重复"}, format="json").status_code == 400

    def test_resources_endpoint(self, auth_client):
        data = auth_client.get(f"{TAGS_URL}/resources").json()["data"]
        assert {item["key"] for item in data["resources"]} == {
            "identity.userinfo",
            "file.uploadfile",
            "approval.approvalinstance",
        }


class TestAssign:
    def test_assign_and_read_back(self, auth_client, superuser):
        user = UserInfo.objects.create(username="tag-api-1", nickname="打标用户")
        tag = Tag.objects.create(name="外包")
        response = auth_client.post(
            f"{TAGS_URL}/assign",
            {"resource": "identity.userinfo", "pk": str(user.pk), "tags": [str(tag.pk)]},
            format="json",
        )
        assert response.status_code == 200, response.data
        assert [item["name"] for item in response.json()["data"]["tags"]] == ["外包"]
        objects = auth_client.get(f"{TAGS_URL}/objects?resource=identity.userinfo&pk={user.pk}").json()["data"]
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
            {"resource": "identity.userinfo", "pk": str(user.pk), "tags": ["00000000-0000-0000-0000-000000000000"]},
            format="json",
        )
        assert response.json()["code"] == 1001

    def test_batch_assign_modes(self, auth_client):
        users = [UserInfo.objects.create(username=f"tag-batch-{i}", nickname=f"批量{i}") for i in range(2)]
        first, second = Tag.objects.create(name="A"), Tag.objects.create(name="B")
        pks = [str(user.pk) for user in users]
        added = auth_client.post(
            f"{TAGS_URL}/batch-assign",
            {"resource": "identity.userinfo", "pks": pks, "tags": [str(first.pk)], "mode": "add"},
            format="json",
        ).json()
        assert added["code"] == 1000 and len(added["data"]["success"]) == 2
        removed = auth_client.post(
            f"{TAGS_URL}/batch-assign",
            {"resource": "identity.userinfo", "pks": pks, "tags": [str(first.pk)], "mode": "remove"},
            format="json",
        ).json()
        assert removed["code"] == 1000
        assert TaggedItem.objects.count() == 0
        replaced = auth_client.post(
            f"{TAGS_URL}/batch-assign",
            {"resource": "identity.userinfo", "pks": pks, "tags": [str(second.pk)], "mode": "replace"},
            format="json",
        ).json()
        assert replaced["code"] == 1000 and TaggedItem.objects.count() == 2

    def test_assign_approval_instance(self, auth_client):
        """审批实例打标：回落 comment 权限点（超管直通），打标后对象序列化器回显。"""
        from approval.models import ApprovalFlow, ApprovalInstance

        flow = ApprovalFlow.objects.create(name="打标流程", code="tag_flow", form_schema=[], version=1)
        instance = ApprovalInstance.objects.create(flow=flow, flow_name=flow.name, title="打标审批单", status="PENDING")
        tag = Tag.objects.create(name="加急")
        response = auth_client.post(
            f"{TAGS_URL}/assign",
            {"resource": "approval.approvalinstance", "pk": str(instance.pk), "tags": [str(tag.pk)]},
            format="json",
        )
        assert response.status_code == 200, response.data
        assert [item["name"] for item in response.json()["data"]["tags"]] == ["加急"]
        rows = auth_client.get("/api/approval/approval-instances", {"title": "打标审批单"}).json()["data"]["results"]
        assert [item["name"] for item in rows[0]["tags"]] == ["加急"]
        # ?tag= 过滤（AND 语义过滤器已接审批实例列表）
        filtered = auth_client.get("/api/approval/approval-instances", {"tag": "加急"}).json()["data"]["results"]
        assert any(row["pk"] == str(instance.pk) for row in filtered)

    def test_normal_user_without_object_permission_rejected(self, normal_user):
        """双门：菜单权限点（403）或业务对象 update 权限回落（1001）任一拦截即通过。"""
        user = UserInfo.objects.create(username="tag-api-3", nickname="打标用户")
        from rest_framework.test import APIClient

        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        response = client.post(
            f"{TAGS_URL}/assign", {"resource": "identity.userinfo", "pk": str(user.pk), "tags": []}, format="json"
        )
        assert response.status_code == 403 or response.json()["code"] == 1001
        assert not TaggedItem.objects.exists()


class TestObjectVisibility:
    """objects 读口对象级校验：请求者看不到目标对象时与「不存在」同响应（404）。

    可见域与各域列表页同源：普通模型走全局数据权限过滤（无授权 = 不可见），
    审批实例走审批域可见域（发起人/审批人/参与人/抄送人可见，与数据授权无关）。
    """

    @staticmethod
    def _tag_object(model, pk, name):
        tag = Tag.objects.create(name=name)
        TaggedItem.objects.create(tag=tag, content_type=ContentType.objects.get_for_model(model), object_id=str(pk))
        return tag

    @staticmethod
    def _granted_viewer(api_client, normal_user, role, menu_factory):
        """带 objects:Tag 权限点的普通用户（通过端点菜单门禁，专测对象级校验）。"""
        menu = menu_factory("objects:Tag", path=TAG_OBJECTS_PERMISSION, method="GET")
        role.menu.add(menu)
        django_cache.clear()  # 权限数据 24h 缓存：授权变更后需失效再取
        api_client.force_authenticate(user=normal_user)
        return normal_user

    @staticmethod
    def _make_approval_instance(creator=None):
        from approval.models import ApprovalFlow, ApprovalInstance

        flow = ApprovalFlow.objects.create(name="可见性流程", code="tag_visible_flow", form_schema=[], version=1)
        return ApprovalInstance.objects.create(
            flow=flow, flow_name=flow.name, title="可见性审批单", status="PENDING", creator=creator
        )

    def test_superuser_reads_each_object_type(self, auth_client):
        """三类可打标对象（用户/文件/审批实例）：可见者照常返回打标情况。"""
        user = UserInfo.objects.create(username="tag-vis-su", nickname="可见性")
        self._tag_object(UserInfo, user.pk, "用户标签")
        resp = auth_client.get(f"{TAGS_URL}/objects", {"resource": "identity.userinfo", "pk": str(user.pk)})
        assert resp.status_code == 200 and [i["name"] for i in resp.json()["data"]["tags"]] == ["用户标签"]

        from file.models import UploadFile

        doc = UploadFile.objects.create(
            filename="tag-vis.txt", filesize=1, mime_type="text/plain", md5sum="md5-tag-vis"
        )
        self._tag_object(UploadFile, doc.pk, "文件标签")
        resp = auth_client.get(f"{TAGS_URL}/objects", {"resource": "file.uploadfile", "pk": str(doc.pk)})
        assert resp.status_code == 200 and [i["name"] for i in resp.json()["data"]["tags"]] == ["文件标签"]

        instance = self._make_approval_instance()
        self._tag_object(type(instance), instance.pk, "审批标签")
        resp = auth_client.get(f"{TAGS_URL}/objects", {"resource": "approval.approvalinstance", "pk": str(instance.pk)})
        assert resp.status_code == 200 and [i["name"] for i in resp.json()["data"]["tags"]] == ["审批标签"]

    def test_user_denied_without_data_grant(self, api_client, normal_user, role, menu_factory):
        """目标用户不在请求者数据权限可见域内（无任何授权）→ 404。"""
        viewer = self._granted_viewer(api_client, normal_user, role, menu_factory)
        target = UserInfo.objects.create(username="tag-vis-target", nickname="被探测")
        self._tag_object(UserInfo, target.pk, "私有标签")
        resp = api_client.get(f"{TAGS_URL}/objects", {"resource": "identity.userinfo", "pk": str(target.pk)})
        assert resp.status_code == 404
        assert TaggedItem.objects.filter(object_id=str(target.pk)).exists()  # 打标关系仍在，只是不可见
        assert str(viewer.pk) != str(target.pk)

    def test_user_visible_with_data_grant(self, api_client, normal_user, role, menu_factory):
        """授权「全部数据」后，可见域内对象的打标情况照常返回。"""
        viewer = self._granted_viewer(api_client, normal_user, role, menu_factory)
        grant = DataPermission.objects.create(
            name="标签可见-全部用户",
            rules=[{"table": "identity.userinfo", "field": "id", "type": "value.all", "value": "*", "match": "all"}],
        )
        viewer.rules.add(grant)
        django_cache.clear()
        target = UserInfo.objects.create(username="tag-vis-granted", nickname="已授权")
        self._tag_object(UserInfo, target.pk, "已授权标签")
        resp = api_client.get(f"{TAGS_URL}/objects", {"resource": "identity.userinfo", "pk": str(target.pk)})
        assert resp.status_code == 200
        assert [i["name"] for i in resp.json()["data"]["tags"]] == ["已授权标签"]

    def test_file_denied_without_data_grant(self, api_client, normal_user, role, menu_factory):
        """目标文件不在请求者数据权限可见域内 → 404；授权后照常返回。"""
        from file.models import UploadFile

        self._granted_viewer(api_client, normal_user, role, menu_factory)
        doc = UploadFile.objects.create(
            filename="tag-denied.txt", filesize=1, mime_type="text/plain", md5sum="md5-deny"
        )
        self._tag_object(UploadFile, doc.pk, "文件私有标签")
        resp = api_client.get(f"{TAGS_URL}/objects", {"resource": "file.uploadfile", "pk": str(doc.pk)})
        assert resp.status_code == 404

        grant = DataPermission.objects.create(
            name="标签可见-全部文件",
            rules=[{"table": "file.uploadfile", "field": "id", "type": "value.all", "value": "*", "match": "all"}],
        )
        normal_user.rules.add(grant)
        django_cache.clear()
        resp = api_client.get(f"{TAGS_URL}/objects", {"resource": "file.uploadfile", "pk": str(doc.pk)})
        assert resp.status_code == 200
        assert [i["name"] for i in resp.json()["data"]["tags"]] == ["文件私有标签"]

    def test_approval_creator_visible_without_data_grant(self, api_client, normal_user, role, menu_factory):
        """审批实例走审批域可见域：发起人无需数据授权即可读自己单据的打标情况。"""
        self._granted_viewer(api_client, normal_user, role, menu_factory)
        instance = self._make_approval_instance(creator=normal_user)
        self._tag_object(type(instance), instance.pk, "我的审批标签")
        resp = api_client.get(f"{TAGS_URL}/objects", {"resource": "approval.approvalinstance", "pk": str(instance.pk)})
        assert resp.status_code == 200
        assert [i["name"] for i in resp.json()["data"]["tags"]] == ["我的审批标签"]

    def test_approval_uninvolved_denied(self, api_client, normal_user, role, menu_factory):
        """与审批单无任何关系（非发起/审批/参与/抄送）→ 404。"""
        self._granted_viewer(api_client, normal_user, role, menu_factory)
        instance = self._make_approval_instance()
        self._tag_object(type(instance), instance.pk, "他人审批标签")
        resp = api_client.get(f"{TAGS_URL}/objects", {"resource": "approval.approvalinstance", "pk": str(instance.pk)})
        assert resp.status_code == 404

    def test_malformed_pk_denied(self, api_client, normal_user, role, menu_factory):
        """主键格式非法（UUID 主键传入任意串）按不可见处理，不 500。"""
        self._granted_viewer(api_client, normal_user, role, menu_factory)
        resp = api_client.get(f"{TAGS_URL}/objects", {"resource": "identity.userinfo", "pk": "not-a-uuid"})
        assert resp.status_code == 404

    def test_unknown_resource_and_empty_pk_unchanged(self, api_client, normal_user, role, menu_factory):
        """白名单外资源仍按业务校验拒绝（1001）；缺 pk 的退化查询仍返回空标签。"""
        self._granted_viewer(api_client, normal_user, role, menu_factory)
        resp = api_client.get(f"{TAGS_URL}/objects", {"resource": "system.role", "pk": "1"})
        assert resp.status_code == 200 and resp.json()["code"] == 1001
        resp = api_client.get(f"{TAGS_URL}/objects", {"resource": "identity.userinfo"})
        assert resp.status_code == 200 and resp.json()["data"]["tags"] == []


class TestFilterAndDeleteProtection:
    def test_list_filter_by_tag_name(self, auth_client):
        tagged = UserInfo.objects.create(username="tag-filter-yes", nickname="筛选")
        other = UserInfo.objects.create(username="tag-filter-no", nickname="筛选")
        tag = Tag.objects.create(name="筛选标签")
        auth_client.post(
            f"{TAGS_URL}/assign",
            {"resource": "identity.userinfo", "pk": str(tagged.pk), "tags": [str(tag.pk)]},
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
            {"resource": "identity.userinfo", "pk": str(first.pk), "tags": [str(tag_a.pk), str(tag_b.pk)]},
            format="json",
        )
        auth_client.post(
            f"{TAGS_URL}/assign",
            {"resource": "identity.userinfo", "pk": str(second.pk), "tags": [str(tag_a.pk)]},
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
            {"resource": "identity.userinfo", "pk": str(user.pk), "tags": [str(tag.pk)]},
            format="json",
        )
        assert auth_client.delete(f"{TAGS_URL}/{tag.pk}").status_code == 400
        assert Tag.objects.filter(pk=tag.pk).exists()

    def test_delete_unused_tag(self, auth_client):
        tag = Tag.objects.create(name="未引用")
        assert auth_client.delete(f"{TAGS_URL}/{tag.pk}").status_code in (200, 204)
        assert not Tag.objects.filter(pk=tag.pk).exists()
