# -*- coding: utf-8 -*-
"""大屏画布窗格 API 集成测试（批次一）。

口径：
1. `layout` 与 `dashboards` 并存——写入窗格即进入画布模式，清空 `layout` 回到轮播模式；
2. 非法窗格（越界 / 重叠 / 未知仪表盘 / 未知类型）一律 400 且响应体带可读原因，
   不得落库半成品数据；
3. 列表/详情返回归一化后的窗格（未声明键被丢弃，便于前端直接消费）。
"""

import pytest

from dataset.models.dataset import Dashboard, Screen

pytestmark = pytest.mark.django_db

SCREEN_URL = "/api/dataset/screens"


@pytest.fixture
def dashboard(superuser):
    return Dashboard.objects.create(name="设计器看板", layout=[], visibility="shared", creator=superuser)


def _pane(pk, dashboard_pk, x=0, y=0, w=6, h=4, **extra):
    return {"pk": pk, "type": "dashboard", "dashboard": dashboard_pk, "x": x, "y": y, "w": w, "h": h, **extra}


class TestLayoutWrite:
    def test_create_with_layout(self, auth_client, dashboard):
        payload = {
            "name": "画布大屏",
            "dashboards": [],
            "layout": [
                _pane("p1", str(dashboard.pk)),
                {"pk": "t1", "type": "text", "x": 6, "y": 0, "w": 6, "h": 4, "text": "月度目标"},
            ],
        }
        body = auth_client.post(SCREEN_URL, payload, format="json").json()
        assert body["code"] == 1000, body
        panes = body["data"]["layout"]
        assert [pane["pk"] for pane in panes] == ["p1", "t1"]
        assert panes[0]["dashboard"] == str(dashboard.pk)

    def test_partial_update_replaces_layout(self, auth_client, dashboard):
        created = auth_client.post(
            SCREEN_URL,
            {"name": "画布大屏-改", "dashboards": [], "layout": [_pane("p1", str(dashboard.pk))]},
            format="json",
        ).json()
        pk = created["data"]["pk"]

        resp = auth_client.patch(
            f"{SCREEN_URL}/{pk}",
            {"layout": [{"pk": "c1", "type": "clock", "x": 0, "y": 0, "w": 3, "h": 2}]},
            format="json",
        ).json()
        # 时钟窗格缺省字号 40（服务端补齐，前端渲染口径一致）
        assert resp["data"]["layout"] == [{"pk": "c1", "type": "clock", "x": 0, "y": 0, "w": 3, "h": 2, "size": 40}]
        assert resp["code"] == 1000, resp
        assert Screen.objects.get(pk=pk).layout[0]["type"] == "clock"

    def test_clear_layout_back_to_carousel(self, auth_client, dashboard):
        created = auth_client.post(
            SCREEN_URL,
            {"name": "画布大屏-清空", "dashboards": [str(dashboard.pk)], "layout": [_pane("p1", str(dashboard.pk))]},
            format="json",
        ).json()
        pk = created["data"]["pk"]
        resp = auth_client.patch(f"{SCREEN_URL}/{pk}", {"layout": []}, format="json").json()
        assert resp["code"] == 1000
        assert resp["data"]["layout"] == []
        # 轮播字段不受影响
        assert resp["data"]["dashboards"] == [str(dashboard.pk)]

    def test_unknown_keys_are_dropped(self, auth_client, dashboard):
        created = auth_client.post(
            SCREEN_URL,
            {
                "name": "画布大屏-净",
                "dashboards": [],
                "layout": [_pane("p1", str(dashboard.pk), color="red", zIndex=9)],
            },
            format="json",
        ).json()
        pane = created["data"]["layout"][0]
        assert "color" not in pane and "zIndex" not in pane


class TestLayoutRejections:
    def _create(self, auth_client, layout, name="画布大屏-校验"):
        return auth_client.post(
            SCREEN_URL,
            {"name": name, "dashboards": [], "layout": layout},
            format="json",
        )

    def test_overlap_rejected(self, auth_client, dashboard):
        resp = self._create(
            auth_client,
            [
                _pane("p1", str(dashboard.pk), x=0, y=0, w=6, h=4),
                _pane("p2", str(dashboard.pk), x=3, y=2, w=6, h=4),
            ],
        )
        assert resp.status_code == 400, resp.json()
        # 文案跟随语言包（zh 已翻译），断言冲突窗格标识而非英文措辞
        assert "p2" in str(resp.json())

    def test_unknown_dashboard_rejected(self, auth_client):
        resp = self._create(auth_client, [_pane("p1", "00000000-0000-0000-0000-000000000000")])
        assert resp.status_code == 400

    def test_exceed_grid_rejected(self, auth_client, dashboard):
        resp = self._create(auth_client, [_pane("p1", str(dashboard.pk), x=8, w=6)])
        assert resp.status_code == 400

    def test_unknown_type_rejected(self, auth_client):
        resp = self._create(
            auth_client,
            [{"pk": "p1", "type": "video", "x": 0, "y": 0, "w": 6, "h": 4}],
        )
        assert resp.status_code == 400

    def test_rejected_payload_does_not_persist(self, auth_client, dashboard):
        self._create(
            auth_client,
            [
                _pane("p1", str(dashboard.pk), x=0, y=0, w=6, h=4),
                _pane("p2", str(dashboard.pk), x=0, y=0, w=6, h=4),
            ],
        )
        assert not Screen.objects.filter(name="画布大屏-校验").exists()

    def test_invalid_layout_on_update_keeps_old_value(self, auth_client, dashboard):
        created = auth_client.post(
            SCREEN_URL,
            {"name": "画布大屏-回滚", "dashboards": [], "layout": [_pane("p1", str(dashboard.pk))]},
            format="json",
        ).json()
        pk = created["data"]["pk"]
        resp = auth_client.patch(
            f"{SCREEN_URL}/{pk}",
            {"layout": [{"pk": "c1", "type": "clock", "x": 0, "y": 0, "w": 13, "h": 2}]},
            format="json",
        )
        assert resp.status_code == 400
        assert Screen.objects.get(pk=pk).layout[0]["pk"] == "p1"


class TestLayoutRead:
    def test_list_and_detail_return_layout(self, auth_client, dashboard):
        created = auth_client.post(
            SCREEN_URL,
            {"name": "画布大屏-读", "dashboards": [], "layout": [_pane("p1", str(dashboard.pk))]},
            format="json",
        ).json()
        pk = created["data"]["pk"]

        detail = auth_client.get(f"{SCREEN_URL}/{pk}").json()
        assert detail["data"]["layout"][0]["pk"] == "p1"

        listed = auth_client.get(SCREEN_URL, {"name": "画布大屏-读"}).json()["data"]["results"]
        assert listed[0]["layout"][0]["pk"] == "p1"


@pytest.fixture
def screen_writer_menus(db):
    """非超管写大屏所需的权限菜单（与 test_analysis_api 的 screen_urls 同款注册）。"""
    from system.models import Menu, MenuMeta

    def _make(name, path, method):
        meta = MenuMeta.objects.create(title=name)
        return Menu.objects.create(
            name=name, path=path, method=method, menu_type=Menu.MenuChoices.PERMISSION, meta=meta
        )

    detail = "api/dataset/screens/(?P<pk>[^/.]+)"
    return [
        _make("list:Screen", "api/dataset/screens$", "GET"),
        _make("create:Screen", "api/dataset/screens$", "POST"),
        _make("partialUpdate:Screen", detail + "$", "PATCH"),
    ]


def _user_client(user, menus):
    from rest_framework.test import APIClient

    from identity.models import UserRole

    role = UserRole.objects.create(name=f"role-{user.username}", code=user.username)
    user.roles.add(role)
    role.menu.set(menus)
    client = APIClient(HTTP_USER_AGENT="pytest-agent")
    client.force_authenticate(user=user)
    return client


class TestDashboardVisibilityOnWrite:
    """引用校验与 Dashboard 读侧可见口径一致：shared 或本人创建（超管全量）。

    低权用户把他人个人仪表盘挂进大屏会被展示端整格静默空——保存时按提交用户
    可见域拒绝（轮播清单与画布窗格两条路径都校验）。
    """

    def test_others_personal_dashboard_rejected(self, superuser, normal_user, screen_writer_menus):
        personal = Dashboard.objects.create(name="他人个人看板", layout=[], visibility="personal", creator=superuser)
        client = _user_client(normal_user, screen_writer_menus)

        # 轮播清单：存在但不可见 → 拒绝
        carousel = client.post(SCREEN_URL, {"name": "越权轮播大屏", "dashboards": [str(personal.pk)]}, format="json")
        assert carousel.status_code == 400
        # 画布窗格：同样拒绝
        canvas = client.post(
            SCREEN_URL,
            {"name": "越权画布大屏", "dashboards": [], "layout": [_pane("p1", str(personal.pk))]},
            format="json",
        )
        assert canvas.status_code == 400
        assert not Screen.objects.filter(name__in=["越权轮播大屏", "越权画布大屏"]).exists()

    def test_creator_own_personal_dashboard_allowed(self, normal_user, screen_writer_menus):
        own = Dashboard.objects.create(name="本人个人看板", layout=[], visibility="personal", creator=normal_user)
        client = _user_client(normal_user, screen_writer_menus)
        resp = client.post(
            SCREEN_URL,
            {"name": "自有看板大屏", "dashboards": [str(own.pk)], "layout": [_pane("p1", str(own.pk))]},
            format="json",
        )
        assert resp.json()["code"] == 1000, resp.json()

    def test_shared_dashboard_referenceable_by_others(self, superuser, normal_user, screen_writer_menus):
        shared = Dashboard.objects.create(name="共享看板", layout=[], visibility="shared", creator=superuser)
        client = _user_client(normal_user, screen_writer_menus)
        resp = client.post(
            SCREEN_URL,
            {"name": "共享引用大屏", "dashboards": [str(shared.pk)], "layout": [_pane("p1", str(shared.pk))]},
            format="json",
        )
        assert resp.json()["code"] == 1000, resp.json()

    def test_superuser_may_reference_any_dashboard(self, auth_client, superuser):
        personal = Dashboard.objects.create(
            name="超管引用个人看板", layout=[], visibility="personal", creator=superuser
        )
        resp = auth_client.post(SCREEN_URL, {"name": "超管大屏", "dashboards": [str(personal.pk)]}, format="json")
        assert resp.json()["code"] == 1000, resp.json()
