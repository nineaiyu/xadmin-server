# -*- coding: utf-8 -*-
"""大屏服务端聚合推送（F2）集成测试。

覆盖：
- collect_screen_cards：canvas/carousel 两种形态的卡片展开、kind 判定
  （number → execute，其余 → aggregate，与前端 ChartCard 取数分支同口径）、
  卡片级权限过滤（allowed_roles）与去重；
- build_screen_data_payload：按观察者聚合（carousel 逐仪表盘一帧 / canvas 单帧）、
  fail-closed 单卡失败进 errors 不中断整帧、数据集被删进 errors；
- ScreenDisplayNotify.screen_data_trigger：触发事件自推 screen_data 帧、rev 取控制态、
  屏被删 / 失去可见性静默跳过、构建异常降级为全屏级错误帧、断开连接不再写帧；
- 组广播驱动：layer.group_send 的触发事件经真实 channel layer 送达各连接各自自推；
- REST refresh 指令立即触发数据推送（switch/page/auto 不触发）；
- beat push_screen_data：离屏不触发不落节流键、在线触发并落键、节流窗口内跳过。
"""

import time
import uuid

import pytest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache

from dataset.analysis_tasks import _screen_has_viewers, push_screen_data
from dataset.models.dataset import Dashboard, Dataset, Screen
from dataset.screen_data import build_screen_data_payload, collect_screen_cards
from dataset.views import analysis as analysis_views
from dataset.ws_screen import (
    MIN_DATA_PUSH_INTERVAL,
    ScreenDisplayNotify,
    apply_screen_command,
    data_push_interval,
    screen_group_name,
    screen_push_due,
    screen_push_ts_key,
)
from system.models import ModelLabelField

pytestmark = pytest.mark.django_db

SCREEN_URL = "/api/dataset/screens"


@pytest.fixture
def model_registry(db):
    """字段注册表：system.userinfo 白名单节点（含 DateTime 趋势字段，与 test_dataset_api 同款）。"""
    root, _ = ModelLabelField.objects.get_or_create(
        name="system.userinfo",
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
        name="推送数据集",
        bound_model="system.userinfo",
        columns=["username", "nickname"],
        filters=[],
        row_limit=1000,
        visibility="shared",
        creator=superuser,
    )


@pytest.fixture
def dashboard_a(superuser, dataset):
    """指标卡 + 柱状聚合卡。"""
    return Dashboard.objects.create(
        name="看板A",
        layout=[
            {"id": "c1", "dataset": str(dataset.pk), "title": "总数", "chart_type": "number"},
            {
                "id": "c2",
                "dataset": str(dataset.pk),
                "title": "按用户",
                "chart_type": "bar",
                "group_by": "username",
                "metric": "count",
            },
        ],
        visibility="shared",
        creator=superuser,
    )


@pytest.fixture
def dashboard_b(superuser, dataset):
    """趋势卡（date_trunc 仅折线卡生效）。"""
    return Dashboard.objects.create(
        name="看板B",
        layout=[
            {
                "id": "c3",
                "dataset": str(dataset.pk),
                "title": "按日趋势",
                "chart_type": "line",
                "group_by": "created_time",
                "metric": "count",
                "date_trunc": "day",
            }
        ],
        visibility="shared",
        creator=superuser,
    )


def _make_screen(creator, dashboards=(), layout=None, visibility="shared"):
    # name 有唯一约束：同一用例可能建多块大屏，后缀随机化；清单项允许直接传 pk 字符串（含已删仪表盘）
    return Screen.objects.create(
        name=f"数据推送大屏-{uuid.uuid4().hex[:8]}",
        dashboards=[str(item.pk) if not isinstance(item, str) else item for item in dashboards],
        layout=layout or [],
        visibility=visibility,
        creator=creator,
    )


def _make_consumer(layer, user, pk, channel_name=None):
    """实例化 consumer 并注入 fake 通道（与 test_screen_control 同手法，帧由 captured 捕获）。"""
    consumer = ScreenDisplayNotify()
    consumer.channel_layer = layer
    consumer.channel_name = channel_name or f"specific.screen-{uuid.uuid4().hex[:10]}"
    consumer.scope = {"user": user, "url_route": {"kwargs": {"pk": str(pk)}}}
    captured, closed = [], []

    async def fake_send_base_json(action, data=None, **kwargs):
        captured.append({"action": action, "data": data})

    async def fake_close(code=None):
        closed.append(code)

    async def fake_accept():
        captured.append({"action": "accepted"})

    consumer.send_base_json = fake_send_base_json
    consumer.close = fake_close
    consumer.accept = fake_accept
    return consumer, captured, closed


class TestCollectScreenCards:
    def test_carousel_expansion_and_kind(self, dashboard_a, dashboard_b, superuser, dataset):
        screen = _make_screen(superuser, [dashboard_a, dashboard_b])
        refs = collect_screen_cards(screen)
        assert [(ref["dashboard"], ref["card"]) for ref in refs] == [
            (str(dashboard_a.pk), "c1"),
            (str(dashboard_a.pk), "c2"),
            (str(dashboard_b.pk), "c3"),
        ]
        # kind 与前端 ChartCard 同口径：number → execute，其余 → aggregate
        assert [ref["kind"] for ref in refs] == ["execute", "aggregate", "aggregate"]
        assert refs[0]["dataset"] == str(dataset.pk)
        assert refs[2]["date_trunc"] == "day"

    def test_canvas_expansion_skips_non_dashboard_panes(self, dashboard_a, dashboard_b, superuser):
        screen = _make_screen(
            superuser,
            layout=[
                {"pk": "p1", "type": "dashboard", "dashboard": str(dashboard_b.pk), "x": 0, "y": 0, "w": 6, "h": 4},
                {"pk": "t1", "type": "text", "x": 6, "y": 0, "w": 6, "h": 4, "text": "看板"},
                {"pk": "p2", "type": "dashboard", "dashboard": str(dashboard_a.pk), "x": 0, "y": 4, "w": 6, "h": 4},
            ],
        )
        refs = collect_screen_cards(screen)
        # 画布按窗格序展开：文本窗格跳过，dashboards 清单不参与
        assert [(ref["dashboard"], ref["card"]) for ref in refs] == [
            (str(dashboard_b.pk), "c3"),
            (str(dashboard_a.pk), "c1"),
            (str(dashboard_a.pk), "c2"),
        ]

    def test_card_level_permission_filter(self, dashboard_a, superuser, normal_user):
        dashboard_a.layout[0]["allowed_roles"] = ["nobody_role"]
        dashboard_a.save(update_fields=["layout"])
        screen = _make_screen(superuser, [dashboard_a])
        # user=None（纯函数全量）/ 超管：不过滤
        assert [ref["card"] for ref in collect_screen_cards(screen)] == ["c1", "c2"]
        assert [ref["card"] for ref in collect_screen_cards(screen, user=superuser)] == ["c1", "c2"]
        # 浏览者：未命中 allowed_roles 的卡片不参与聚合（与仪表盘读取侧同口径）
        assert [ref["card"] for ref in collect_screen_cards(screen, user=normal_user)] == ["c2"]

    def test_dedupe_and_missing_references(self, dashboard_a, superuser):
        screen = _make_screen(superuser, [dashboard_a, dashboard_a, "00000000-0000-0000-0000-000000000000"])
        # 同仪表盘重复清单只展开一次；已删仪表盘跳过
        assert [ref["card"] for ref in collect_screen_cards(screen)] == ["c1", "c2"]

    def test_empty_screen_yields_nothing(self, superuser):
        screen = _make_screen(superuser)
        assert collect_screen_cards(screen) == []


class TestBuildScreenDataPayload:
    def test_carousel_one_frame_per_dashboard(self, dashboard_a, dashboard_b, superuser):
        screen = _make_screen(superuser, [dashboard_a, dashboard_b])
        payloads = build_screen_data_payload(superuser, screen, rev=3)
        assert [payload["dashboard"] for payload in payloads] == [str(dashboard_a.pk), str(dashboard_b.pk)]
        first = payloads[0]
        assert first["screen"] == str(screen.pk)
        assert first["rev"] == 3
        assert isinstance(first["ts"], int)
        assert first["errors"] == []
        c1 = next(card for card in first["cards"] if card["card"] == "c1")
        assert c1["kind"] == "execute"
        assert set(c1["data"]) == {"columns", "rows", "total", "limit"}
        c2 = next(card for card in first["cards"] if card["card"] == "c2")
        assert c2["kind"] == "aggregate"
        assert "series" in c2["data"]
        # 趋势卡：date_trunc 生效（series 键存在，行级 fail-closed 下可空）
        c3 = next(card for card in payloads[1]["cards"] if card["card"] == "c3")
        assert c3["kind"] == "aggregate"
        assert "series" in c3["data"]

    def test_canvas_single_frame_without_dashboard(self, dashboard_a, dashboard_b, superuser):
        screen = _make_screen(
            superuser,
            layout=[
                {"pk": "p1", "type": "dashboard", "dashboard": str(dashboard_b.pk), "x": 0, "y": 0, "w": 6, "h": 4},
                {"pk": "p2", "type": "dashboard", "dashboard": str(dashboard_a.pk), "x": 0, "y": 4, "w": 6, "h": 4},
            ],
        )
        payloads = build_screen_data_payload(superuser, screen, rev=0)
        assert len(payloads) == 1
        assert payloads[0]["dashboard"] is None
        assert [card["card"] for card in payloads[0]["cards"]] == ["c3", "c1", "c2"]

    def test_fail_closed_card_goes_to_errors_without_breaking_frame(self, dashboard_a, superuser):
        # sum 缺 value_field：aggregate_dataset fail-closed 报错，同帧其余卡片不受影响
        dashboard_a.layout[1]["metric"] = "sum"
        dashboard_a.save(update_fields=["layout"])
        screen = _make_screen(superuser, [dashboard_a])
        payloads = build_screen_data_payload(superuser, screen, rev=0)
        assert len(payloads) == 1
        assert [card["card"] for card in payloads[0]["cards"]] == ["c1"]
        assert [error["card"] for error in payloads[0]["errors"]] == ["c2"]
        assert payloads[0]["errors"][0]["detail"]

    def test_deleted_dataset_goes_to_errors(self, dashboard_a, superuser):
        dataset_pk = str(dashboard_a.layout[0]["dataset"])
        Dataset.objects.filter(pk=dataset_pk).delete()
        screen = _make_screen(superuser, [dashboard_a])
        payloads = build_screen_data_payload(superuser, screen, rev=0)
        payload = payloads[0]
        # 两张卡引用同一数据集：全部进 errors（detail 与数据行均不再可读）
        assert [error["card"] for error in payload["errors"]] == ["c1", "c2"]
        assert all(error["detail"] for error in payload["errors"])
        assert payload["cards"] == []

    def test_empty_screen_builds_no_frames(self, superuser):
        screen = _make_screen(superuser)
        assert build_screen_data_payload(superuser, screen, rev=0) == []


class TestScreenDataTriggerConsumer:
    def test_trigger_pushes_frames_with_control_rev(self, dashboard_a, dashboard_b, superuser):
        screen = _make_screen(superuser, [dashboard_a, dashboard_b])
        consumer, captured, closed = _make_consumer(get_channel_layer(), superuser, screen.pk)
        async_to_sync(consumer.connect)()
        assert closed == []
        before = len(captured)

        # 下发过一次 refresh：rev 单调递增，数据帧应携带当前控制态版本
        apply_screen_command(screen, "refresh")
        async_to_sync(consumer.screen_data_trigger)({"type": "screen_data_trigger"})

        frames = [item for item in captured[before:] if item["action"] == "screen_data"]
        assert len(frames) == 2  # carousel：逐仪表盘一帧
        assert all(frame["data"]["screen"] == str(screen.pk) for frame in frames)
        assert all(frame["data"]["rev"] == 1 for frame in frames)
        assert frames[0]["data"]["dashboard"] == str(dashboard_a.pk)
        assert frames[1]["data"]["dashboard"] == str(dashboard_b.pk)
        assert [card["card"] for card in frames[0]["data"]["cards"]] == ["c1", "c2"]

    def test_trigger_canvas_single_frame(self, dashboard_a, superuser):
        screen = _make_screen(
            superuser,
            layout=[
                {"pk": "p1", "type": "dashboard", "dashboard": str(dashboard_a.pk), "x": 0, "y": 0, "w": 6, "h": 4}
            ],
        )
        consumer, captured, _closed = _make_consumer(get_channel_layer(), superuser, screen.pk)
        async_to_sync(consumer.connect)()
        before = len(captured)
        async_to_sync(consumer.screen_data_trigger)({"type": "screen_data_trigger"})
        frames = [item for item in captured[before:] if item["action"] == "screen_data"]
        assert len(frames) == 1
        assert frames[0]["data"]["dashboard"] is None

    def test_trigger_silent_when_screen_deleted(self, superuser):
        # 已删屏 connect 也进不来（4403），此处仅验证处理器对脏触发的兜底
        consumer, captured, _closed = _make_consumer(get_channel_layer(), superuser, uuid.uuid4())
        async_to_sync(consumer.connect)()
        before = len(captured)
        async_to_sync(consumer.screen_data_trigger)({"type": "screen_data_trigger"})
        assert len(captured) == before

    def test_trigger_silent_when_visibility_revoked(self, superuser, normal_user, dashboard_a):
        personal = _make_screen(superuser, [dashboard_a], visibility="personal")
        consumer, captured, _closed = _make_consumer(get_channel_layer(), normal_user, personal.pk)
        # 不走 connect（personal 对该用户会被拒），手动对齐 connect 注入的连接上下文
        consumer.user = normal_user
        consumer.pk = str(personal.pk)
        async_to_sync(consumer.screen_data_trigger)({"type": "screen_data_trigger"})
        assert captured == []

    def test_trigger_skips_when_disconnected(self, superuser, dashboard_a):
        screen = _make_screen(superuser, [dashboard_a])
        consumer, captured, _closed = _make_consumer(get_channel_layer(), superuser, screen.pk)
        consumer.disconnected = True
        async_to_sync(consumer.screen_data_trigger)({"type": "screen_data_trigger"})
        assert captured == []

    def test_trigger_build_failure_degrades_to_error_frame(self, dashboard_a, superuser, monkeypatch):
        screen = _make_screen(superuser, [dashboard_a])
        consumer, captured, _closed = _make_consumer(get_channel_layer(), superuser, screen.pk)
        async_to_sync(consumer.connect)()

        def _boom(user, screen_obj, rev):
            raise RuntimeError("boom")

        monkeypatch.setattr("dataset.ws_screen.build_screen_data_payload", _boom)
        before = len(captured)
        async_to_sync(consumer.screen_data_trigger)({"type": "screen_data_trigger"})
        frames = [item for item in captured[before:] if item["action"] == "screen_data"]
        # 构建失败不让连接死：降级为全屏级错误帧（card="*"）
        assert len(frames) == 1
        assert frames[0]["data"]["cards"] == []
        assert [error["card"] for error in frames[0]["data"]["errors"]] == ["*"]
        assert frames[0]["data"]["errors"][0]["detail"]


class TestGroupBroadcastTrigger:
    def test_group_send_reaches_each_observer(self, dashboard_a, dashboard_b, superuser, normal_user):
        """组广播只投递无载荷触发事件：各连接各自聚合、各自自推。"""
        screen = _make_screen(superuser, [dashboard_a, dashboard_b])
        layer = get_channel_layer()
        consumer_a, captured_a, _closed_a = _make_consumer(layer, superuser, screen.pk)
        consumer_b, captured_b, _closed_b = _make_consumer(layer, normal_user, screen.pk)
        async_to_sync(consumer_a.connect)()
        async_to_sync(consumer_b.connect)()

        async_to_sync(layer.group_send)(screen_group_name(screen.pk), {"type": "screen_data_trigger"})
        for consumer, captured in ((consumer_a, captured_a), (consumer_b, captured_b)):
            event = async_to_sync(layer.receive)(consumer.channel_name)
            assert event == {"type": "screen_data_trigger"}  # 无载荷：聚合上下文以连接自身为准
            before = len(captured)
            async_to_sync(consumer.screen_data_trigger)(event)
            frames = [item for item in captured[before:] if item["action"] == "screen_data"]
            assert len(frames) == 2
            assert all(frame["data"]["screen"] == str(screen.pk) for frame in frames)
            assert all(frame["data"]["errors"] == [] for frame in frames)

    def test_refresh_command_drives_trigger_event(self, dashboard_a, superuser, auth_client):
        """REST refresh 指令：控制帧之外追加一次 data_trigger（经真实 channel layer 送达）。"""
        screen = _make_screen(superuser, [dashboard_a])
        layer = get_channel_layer()
        consumer, captured, _closed = _make_consumer(layer, superuser, screen.pk)
        async_to_sync(consumer.connect)()

        resp = auth_client.post(f"{SCREEN_URL}/{screen.pk}/command", {"command": "refresh"}, format="json").json()
        assert resp["code"] == 1000, resp

        first = async_to_sync(layer.receive)(consumer.channel_name)
        second = async_to_sync(layer.receive)(consumer.channel_name)
        assert (first["type"], second["type"]) == ("screen_command", "screen_data_trigger")
        async_to_sync(consumer.screen_data_trigger)(second)
        assert captured[-1]["action"] == "screen_data"
        assert captured[-1]["data"]["rev"] == 1

    def test_switch_page_auto_do_not_trigger_data_push(self, dashboard_a, superuser, auth_client, monkeypatch):
        screen = _make_screen(superuser, [dashboard_a])
        called = []
        monkeypatch.setattr(analysis_views, "broadcast_screen_data_trigger", lambda pk: called.append(str(pk)))
        for payload in (
            {"command": "switch", "dashboard_pk": str(dashboard_a.pk)},
            {"command": "page", "index": 0},
            {"command": "auto"},
        ):
            resp = auth_client.post(f"{SCREEN_URL}/{screen.pk}/command", payload, format="json").json()
            assert resp["code"] == 1000, resp
        assert called == []

    def test_refresh_command_triggers_data_push(self, dashboard_a, superuser, auth_client, monkeypatch):
        screen = _make_screen(superuser, [dashboard_a])
        called = []
        monkeypatch.setattr(analysis_views, "broadcast_screen_data_trigger", lambda pk: called.append(str(pk)))
        resp = auth_client.post(f"{SCREEN_URL}/{screen.pk}/command", {"command": "refresh"}, format="json").json()
        assert resp["code"] == 1000, resp
        assert called == [str(screen.pk)]


class TestPushScreenDataTask:
    def test_offline_screen_not_triggered_and_not_marked(self, dashboard_a, superuser, monkeypatch):
        screen = _make_screen(superuser, [dashboard_a])
        triggered = []
        monkeypatch.setattr("dataset.ws_screen.broadcast_screen_data_trigger", lambda pk: triggered.append(str(pk)))
        assert push_screen_data() == 0
        assert triggered == []
        # 离屏不落节流键：观众上线后能尽快收到首帧
        assert cache.get(screen_push_ts_key(screen.pk)) is None

    def test_online_screen_triggered_then_throttled(self, dashboard_a, superuser, monkeypatch):
        screen = _make_screen(superuser, [dashboard_a])
        layer = get_channel_layer()
        consumer, _captured, _closed = _make_consumer(layer, superuser, screen.pk)
        async_to_sync(consumer.connect)()
        triggered = []
        monkeypatch.setattr("dataset.ws_screen.broadcast_screen_data_trigger", lambda pk: triggered.append(str(pk)))

        assert push_screen_data() == 1
        assert triggered == [str(screen.pk)]
        assert isinstance(cache.get(screen_push_ts_key(screen.pk)), int)

        # 节流窗口（refresh=60s）内重复运行：跳过
        assert push_screen_data() == 0
        assert triggered == [str(screen.pk)]

    def test_stale_throttle_key_allows_push(self, dashboard_a, superuser, monkeypatch):
        screen = _make_screen(superuser, [dashboard_a])
        layer = get_channel_layer()
        consumer, _captured, _closed = _make_consumer(layer, superuser, screen.pk)
        async_to_sync(consumer.connect)()
        # 上次推送早于 refresh 周期：视为到期，允许再推
        cache.set(screen_push_ts_key(screen.pk), int(time.time()) - 3600, 7200)
        triggered = []
        monkeypatch.setattr("dataset.ws_screen.broadcast_screen_data_trigger", lambda pk: triggered.append(str(pk)))
        assert push_screen_data() == 1
        assert triggered == [str(screen.pk)]

    def test_beat_registration(self):
        from common.celery.decorator import get_register_period_tasks

        entries = [detail for task in get_register_period_tasks() for detail in task.values()]
        match = [detail for detail in entries if detail["task"].endswith("push_screen_data")]
        assert len(match) == 1
        assert match[0]["interval"] == 15
        assert match[0]["module"] == "analysis"


class TestPushHelpers:
    def test_screen_push_due_throttle_and_clamp(self, superuser, dashboard_a):
        screen = _make_screen(superuser, [dashboard_a])  # refresh 默认 60
        now = int(time.time())
        assert screen_push_due(screen, now=now) is True  # 未推送过
        cache.set(screen_push_ts_key(screen.pk), now - 5, 7200)
        assert screen_push_due(screen, now=now) is False  # 窗口内
        # 小 refresh 钳到 10s 下限：9s 前推送仍算窗口内，10s 前放行
        screen.refresh = 3
        cache.set(screen_push_ts_key(screen.pk), now - MIN_DATA_PUSH_INTERVAL + 1, 7200)
        assert screen_push_due(screen, now=now) is False
        cache.set(screen_push_ts_key(screen.pk), now - MIN_DATA_PUSH_INTERVAL, 7200)
        assert screen_push_due(screen, now=now) is True

    def test_data_push_interval_clamps_small_refresh(self, superuser, dashboard_a):
        screen = _make_screen(superuser, [dashboard_a])
        screen.refresh = 3
        assert data_push_interval(screen) == MIN_DATA_PUSH_INTERVAL  # 10s 下限钳位
        screen.refresh = 0
        assert data_push_interval(screen) == MIN_DATA_PUSH_INTERVAL
        screen.refresh = 120
        assert data_push_interval(screen) == 120

    def test_layer_without_get_layers_assumes_online(self):
        assert _screen_has_viewers(object(), "any_group") is True
