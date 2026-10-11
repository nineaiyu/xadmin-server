# -*- coding: utf-8 -*-
"""大屏模板（ScreenTemplate）API 集成测试。

口径与大屏同源：

1. CRUD 走可见性过滤（personal 仅创建者、shared 全员、超管绕过）；
2. 行级归属守卫：非创建者修改 / 删除 → 1003，`is_owner` 随列表下发；
3. 载荷与自由画布同形：空模板 / 越界 / 引用不可见一律 400，且不落库半成品数据。
"""

import pytest

from dataset.models.dataset import Dashboard, ScreenTemplate
from identity.models import UserInfo

pytestmark = pytest.mark.django_db

TEMPLATE_URL = "/api/dataset/screen-templates"


@pytest.fixture
def template_urls():
    from system.models import Menu, MenuMeta

    def _make(name, path, method):
        meta = MenuMeta.objects.create(title=name)
        return Menu.objects.create(
            name=name, path=path, method=method, menu_type=Menu.MenuChoices.PERMISSION, meta=meta
        )

    detail = "api/dataset/screen-templates/(?P<pk>[^/.]+)"
    return [
        _make("list:ScreenTemplate", "api/dataset/screen-templates$", "GET"),
        _make("create:ScreenTemplate", "api/dataset/screen-templates$", "POST"),
        _make("retrieve:ScreenTemplate", detail + "$", "GET"),
        _make("partialUpdate:ScreenTemplate", detail + "$", "PATCH"),
        _make("destroy:ScreenTemplate", detail + "$", "DELETE"),
        _make("batchDestroy:ScreenTemplate", "api/dataset/screen-templates/batch-destroy$", "POST"),
        _make("versions:ScreenTemplate", detail + "/versions$", "GET"),
        _make("rollback:ScreenTemplate", detail + "/rollback$", "POST"),
    ]


def grant(user, menus):
    from identity.models import UserRole

    role = UserRole.objects.create(name=f"role-{user.username}", code=user.username)
    user.roles.add(role)
    role.menu.set(menus)
    return role


def _client_for(user, menus):
    from rest_framework.test import APIClient

    grant(user, menus)
    client = APIClient(HTTP_USER_AGENT="pytest-agent")
    client.force_authenticate(user=user)
    return client


def _pane(pk="p1", pane_type="text", **extra):
    pane = {"pk": pk, "type": pane_type, "x": 0, "y": 0, "w": 320, "h": 200}
    pane.update(extra)
    return pane


class TestTemplateCrud:
    def test_anonymous_rejected(self, api_client):
        assert api_client.get(TEMPLATE_URL).status_code == 401

    def test_create_list_retrieve(self, auth_client):
        created = auth_client.post(
            TEMPLATE_URL,
            {
                "name": "宣传屏骨架",
                "free_layout": {"panes": [_pane(text="标题", rotate=15, animation="fade", text_format="rich")]},
            },
            format="json",
        ).json()
        assert created["code"] == 1000, created
        pk = created["data"]["pk"]
        pane = created["data"]["free_layout"]["panes"][0]
        # 画幅缺省由服务端补齐；窗格新字段落库
        assert (created["data"]["free_layout"]["design_w"], created["data"]["free_layout"]["design_h"]) == (1920, 1080)
        assert (pane["rotate"], pane["animation"], pane["text_format"]) == (15, "fade", "rich")

        listed = auth_client.get(TEMPLATE_URL).json()
        assert {item["name"] for item in listed["data"]["results"]} == {"宣传屏骨架"}
        detail = auth_client.get(f"{TEMPLATE_URL}/{pk}").json()
        assert detail["data"]["free_layout"]["panes"][0]["text"] == "标题"

    def test_partial_update_replaces_panes(self, auth_client):
        created = auth_client.post(
            TEMPLATE_URL,
            {"name": "改前", "free_layout": {"panes": [_pane()]}},
            format="json",
        ).json()
        pk = created["data"]["pk"]
        patched = auth_client.patch(
            f"{TEMPLATE_URL}/{pk}",
            {"name": "改后", "free_layout": {"panes": [_pane("p9", "clock")]}},
            format="json",
        ).json()
        assert patched["code"] == 1000, patched
        panes = patched["data"]["free_layout"]["panes"]
        assert [item["pk"] for item in panes] == ["p9"]
        assert panes[0]["size"] == 40  # 时钟缺省字号由服务端补齐

    def test_batch_destroy(self, auth_client):
        first = auth_client.post(
            TEMPLATE_URL, {"name": "批删一", "free_layout": {"panes": [_pane()]}}, format="json"
        ).json()
        second = auth_client.post(
            TEMPLATE_URL, {"name": "批删二", "free_layout": {"panes": [_pane()]}}, format="json"
        ).json()
        pks = [first["data"]["pk"], second["data"]["pk"]]
        assert auth_client.post(f"{TEMPLATE_URL}/batch-destroy", pks, format="json").status_code == 200
        assert not ScreenTemplate.objects.filter(pk__in=pks).exists()


class TestTemplateVisibility:
    def test_personal_hidden_shared_visible_and_write_guarded(self, auth_client, normal_user, template_urls):
        # 超管（auth_client）建共享模板；甲（normal_user）自建私有模板
        shared = auth_client.post(
            TEMPLATE_URL,
            {"name": "值班骨架", "visibility": "shared", "free_layout": {"panes": [_pane()]}},
            format="json",
        ).json()
        assert shared["code"] == 1000, shared
        mine = ScreenTemplate.objects.create(name="甲的私有骨架", creator=normal_user, free_layout={"panes": [_pane()]})

        # 甲自己的可见列表：共享 + 自己的私有
        mine_client = _client_for(normal_user, template_urls)
        mine_visible = {item["name"] for item in mine_client.get(TEMPLATE_URL).json()["data"]["results"]}
        assert {"值班骨架", "甲的私有骨架"} <= mine_visible

        # 乙（无关用户）：只看得见共享，甲的私有不可见
        other = UserInfo.objects.create_user(username="lisi-template", password="Test@123456")
        client = _client_for(other, template_urls)
        visible = {item["name"] for item in client.get(TEMPLATE_URL).json()["data"]["results"]}
        assert "值班骨架" in visible and "甲的私有骨架" not in visible

        # 共享只读：非创建者改 / 删都被守卫拒绝
        shared_pk = shared["data"]["pk"]
        patched = client.patch(f"{TEMPLATE_URL}/{shared_pk}", {"name": "改名"}, format="json").json()
        assert patched["code"] == 1003
        deleted = client.delete(f"{TEMPLATE_URL}/{shared_pk}").json()
        assert deleted["code"] == 1003
        assert ScreenTemplate.objects.filter(pk=shared_pk).exists()

        # 自己的模板可改可删
        own = mine_client.patch(f"{TEMPLATE_URL}/{mine.pk}", {"name": "甲的私有骨架改"}, format="json").json()
        assert own["code"] == 1000, own
        assert mine_client.delete(f"{TEMPLATE_URL}/{mine.pk}").status_code == 200

    def test_is_owner_flag_marks_row(self, auth_client, template_urls):
        created = auth_client.post(
            TEMPLATE_URL,
            {"name": "归属标记", "visibility": "shared", "free_layout": {"panes": [_pane()]}},
            format="json",
        ).json()
        listed = auth_client.get(TEMPLATE_URL).json()["data"]["results"]
        row = next(item for item in listed if item["pk"] == created["data"]["pk"])
        assert row["is_owner"] is True


class TestTemplateValidation:
    def test_empty_template_rejected(self, auth_client):
        response = auth_client.post(TEMPLATE_URL, {"name": "空模板", "free_layout": {"panes": []}}, format="json")
        assert response.status_code == 400

    def test_pane_beyond_design_rejected(self, auth_client):
        response = auth_client.post(
            TEMPLATE_URL,
            {"name": "越界模板", "free_layout": {"design_w": 1280, "design_h": 720, "panes": [_pane(x=1000, w=400)]}},
            format="json",
        )
        assert response.status_code == 400
        assert not ScreenTemplate.objects.filter(name="越界模板").exists()

    def test_invisible_dashboard_reference_rejected(self, auth_client, normal_user, template_urls):
        # 普通用户挂入自己不可见的「他人私有仪表盘」：与保存大屏同口径拒绝
        hidden = Dashboard.objects.create(name="他人私有看板", layout=[], visibility="personal", creator=normal_user)
        client = _client_for(UserInfo.objects.create_user(username="wangwu-template"), template_urls)
        response = client.post(
            TEMPLATE_URL,
            {
                "name": "引用不可见",
                "free_layout": {"panes": [_pane(pane_type="dashboard", dashboard=str(hidden.pk))]},
            },
            format="json",
        )
        assert response.status_code == 400
        assert not ScreenTemplate.objects.filter(name="引用不可见").exists()


CLIPBOARD_URL = "/api/dataset/screen-clipboard"


@pytest.fixture
def clipboard_urls():
    from system.models import Menu, MenuMeta

    def _make(name, path, method):
        meta = MenuMeta.objects.create(title=name)
        return Menu.objects.create(
            name=name, path=path, method=method, menu_type=Menu.MenuChoices.PERMISSION, meta=meta
        )

    detail = "api/dataset/screen-clipboard/(?P<pk>[^/.]+)"
    return [
        _make("list:ScreenClipboard", "api/dataset/screen-clipboard$", "GET"),
        _make("create:ScreenClipboard", "api/dataset/screen-clipboard$", "POST"),
        _make("retrieve:ScreenClipboard", detail + "$", "GET"),
        _make("partialUpdate:ScreenClipboard", detail + "$", "PATCH"),
        _make("destroy:ScreenClipboard", detail + "$", "DELETE"),
    ]


class TestTemplateVersions:
    """模板版本历史与回滚（ADR-090 D4）：保存即记版、保留上限裁剪、回滚可逆。"""

    def test_versions_recorded_and_pruned(self, auth_client, settings):
        settings.SCREEN_TEMPLATE_VERSION_KEEP = 3
        from dataset.views.analysis import _version_keep, snapshot_template_version

        # 保留数允许 settings 覆盖（生产缺省 10，测试调小到 3 以便验证裁剪）
        assert _version_keep() == 3

        created = auth_client.post(
            TEMPLATE_URL, {"name": "版0", "free_layout": {"panes": [_pane()]}}, format="json"
        ).json()["data"]
        template = ScreenTemplate.objects.get(pk=created["pk"])
        assert template.versions.count() == 1

        for index in range(1, 6):
            template.name = f"版{index}"
            template.save(update_fields=["name"])
            snapshot_template_version(template)
        # 保留最近 3 版：版本号最大的是最新
        versions = list(template.versions.values_list("version", flat=True))
        assert versions == [6, 5, 4]
        assert template.versions.first().name == "版5"

    def test_rollback_restores_and_records_new_version(self, auth_client):
        created = auth_client.post(
            TEMPLATE_URL,
            {"name": "回滚前", "group": "甲组", "free_layout": {"panes": [_pane(text="旧")]}},
            format="json",
        ).json()["data"]
        pk = created["pk"]
        auth_client.patch(
            f"{TEMPLATE_URL}/{pk}",
            {"name": "回滚目标", "free_layout": {"panes": [_pane(text="新")]}},
            format="json",
        )

        listed = auth_client.get(f"{TEMPLATE_URL}/{pk}/versions").json()
        assert listed["code"] == 1000
        versions = [row["version"] for row in listed["data"]]
        assert versions == [2, 1]

        rolled = auth_client.post(f"{TEMPLATE_URL}/{pk}/rollback", {"version": 1}, format="json").json()
        assert rolled["code"] == 1000
        assert rolled["data"]["name"] == "回滚前"
        assert rolled["data"]["group"] == "甲组"
        assert rolled["data"]["free_layout"]["panes"][0]["text"] == "旧"
        # 回滚本身也记一版 → 可以再回滚回来
        assert [row["version"] for row in auth_client.get(f"{TEMPLATE_URL}/{pk}/versions").json()["data"]] == [
            3,
            2,
            1,
        ]

    def test_rollback_unknown_version_rejected(self, auth_client):
        created = auth_client.post(
            TEMPLATE_URL, {"name": "缺版本", "free_layout": {"panes": [_pane()]}}, format="json"
        ).json()["data"]
        res = auth_client.post(f"{TEMPLATE_URL}/{created['pk']}/rollback", {"version": 99}, format="json").json()
        assert res["code"] == 1001


class TestScreenClipboard:
    """云端剪贴板（ADR-090 D4）：按用户单槽 upsert、只能看到自己的槽位。"""

    def test_anonymous_rejected(self, api_client):
        assert api_client.get(CLIPBOARD_URL).status_code == 401

    def test_upsert_single_slot(self, auth_client, clipboard_urls):
        first = auth_client.post(
            CLIPBOARD_URL, {"free_layout": {"panes": [_pane(text="第一次")]}}, format="json"
        ).json()
        assert first["code"] == 1000
        pk = first["data"]["pk"]

        second = auth_client.post(
            CLIPBOARD_URL, {"free_layout": {"panes": [_pane(text="第二次")]}}, format="json"
        ).json()
        assert second["code"] == 1000
        assert second["data"]["pk"] == pk  # 同一个槽位（upsert）

        rows = auth_client.get(CLIPBOARD_URL).json()["data"]["results"]
        assert len(rows) == 1
        assert rows[0]["free_layout"]["panes"][0]["text"] == "第二次"
        assert (rows[0]["free_layout"]["design_w"], rows[0]["free_layout"]["design_h"]) == (1920, 1080)

    def test_empty_payload_allowed_and_cleared(self, auth_client, clipboard_urls):
        created = auth_client.post(CLIPBOARD_URL, {"free_layout": {}}, format="json").json()
        assert created["code"] == 1000
        pk = created["data"]["pk"]
        assert auth_client.delete(f"{CLIPBOARD_URL}/{pk}").json()["code"] == 1000
        assert auth_client.get(CLIPBOARD_URL).json()["data"]["results"] == []

    def test_other_user_slot_invisible(self, auth_client, clipboard_urls):
        auth_client.post(CLIPBOARD_URL, {"free_layout": {"panes": [_pane(text="我的")]}}, format="json")
        other = UserInfo.objects.create_user(username="clip-other", password="x")
        other_client = _client_for(other, clipboard_urls)
        assert other_client.get(CLIPBOARD_URL).json()["data"]["results"] == []
