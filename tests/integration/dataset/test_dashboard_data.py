# -*- coding: utf-8 -*-
"""仪表盘（工作台）服务端聚合推送集成测试。

覆盖：
- collect_dashboard_cards：卡片展开、kind 判定（number → execute，其余 → aggregate，
  与前端 ChartCard 取数分支同口径）、卡片级权限过滤（allowed_roles）与坏卡跳过；
- build_dashboard_data_payload：按观察者聚合单帧、fail-closed 单卡失败进 errors
  不中断整帧、空布局空帧；
- DashboardDataNotify：连接即自推首帧、匿名 / personal 越权 4403/4401、触发事件
  自推 dashboard_data 帧、合并窗口、断开不写帧、盘被删 / 可见性收回静默跳过、
  构建异常降级为全盘级错误帧、未知上行动作回执 1001；
- 组广播驱动：layer.group_send 的触发事件经真实 channel layer 送达各连接各自自推；
- beat push_dashboard_data：离盘不触发不落节流键、在线触发并落键、节流窗口内跳过。
"""

import time
import uuid

import pytest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache

from dataset.analysis_tasks import _group_has_viewers, push_dashboard_data
from dataset.dashboard_data import build_dashboard_data_payload, collect_dashboard_cards
from dataset.models.dataset import Dashboard, Dataset
from dataset.ws_dashboard import (
    MIN_DATA_PUSH_INTERVAL,
    TRIGGER_MERGE_WINDOW,
    DashboardDataNotify,
    claim_dashboard_push,
    dashboard_group_name,
    dashboard_push_due,
    dashboard_push_ts_key,
)
from system.models import ModelLabelField

pytestmark = pytest.mark.django_db


@pytest.fixture
def model_registry(db):
    """字段注册表：identity.userinfo 白名单节点（与 test_dataset_api 同款）。"""
    root, _ = ModelLabelField.objects.get_or_create(
        name="identity.userinfo",
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
        bound_model="identity.userinfo",
        columns=["username", "nickname"],
        filters=[],
        row_limit=1000,
        visibility="shared",
        creator=superuser,
    )


def _make_dashboard(creator, cards=(), visibility="shared", **kwargs):
    """name 有唯一约束：同一用例可能建多块仪表盘，后缀随机化。"""
    return Dashboard.objects.create(
        name=f"推送看板-{uuid.uuid4().hex[:8]}",
        layout=list(cards),
        visibility=visibility,
        creator=creator,
        **kwargs,
    )


@pytest.fixture
def dashboard_a(superuser, dataset):
    """指标卡 + 柱状聚合卡。"""
    return _make_dashboard(
        superuser,
        [
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
    )


def _make_consumer(layer, user, pk, channel_name=None):
    """实例化 consumer 并注入 fake 通道（与 test_screen_data 同手法，帧由 captured 捕获）。"""
    consumer = DashboardDataNotify()
    consumer.channel_layer = layer
    consumer.channel_name = channel_name or f"specific.dashboard-{uuid.uuid4().hex[:10]}"
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


class TestCollectDashboardCards:
    def test_expansion_and_kind(self, dashboard_a, dataset):
        refs = collect_dashboard_cards(dashboard_a)
        assert [ref["card"] for ref in refs] == ["c1", "c2"]
        # kind 与前端 ChartCard 同口径：number → execute，其余 → aggregate
        assert [ref["kind"] for ref in refs] == ["execute", "aggregate"]
        assert refs[0]["dataset"] == str(dataset.pk)
        assert refs[0]["dashboard"] == str(dashboard_a.pk)

    def test_card_level_permission_filter(self, dashboard_a, superuser, normal_user):
        dashboard_a.layout[0]["allowed_roles"] = ["nobody_role"]
        dashboard_a.save(update_fields=["layout"])
        # user=None（纯函数全量）/ 超管：不过滤
        assert [ref["card"] for ref in collect_dashboard_cards(dashboard_a)] == ["c1", "c2"]
        assert [ref["card"] for ref in collect_dashboard_cards(dashboard_a, user=superuser)] == ["c1", "c2"]
        # 浏览者：未命中 allowed_roles 的卡片不参与聚合（与仪表盘读取侧同口径）
        assert [ref["card"] for ref in collect_dashboard_cards(dashboard_a, user=normal_user)] == ["c2"]

    def test_creator_bypasses_card_roles(self, dashboard_a, normal_user):
        """创建者旁路：自己仪表盘上未含自身角色的卡片也要推送（与 REST 读侧一致）。"""
        dashboard_a.layout[0]["allowed_roles"] = ["nobody_role"]
        dashboard_a.creator = normal_user
        dashboard_a.save(update_fields=["layout", "creator"])
        assert [ref["card"] for ref in collect_dashboard_cards(dashboard_a, user=normal_user)] == ["c1", "c2"]

    def test_dedupe_and_broken_cards_skipped(self, dashboard_a, superuser, dataset):
        dashboard_a.layout = [
            *dashboard_a.layout,
            {"id": "c1", "dataset": str(dataset.pk), "title": "重复卡", "chart_type": "number"},
            {"id": "", "dataset": str(dataset.pk), "title": "缺 id"},
            {"id": "c9", "title": "缺 dataset", "chart_type": "number"},
        ]
        dashboard_a.save(update_fields=["layout"])
        assert [ref["card"] for ref in collect_dashboard_cards(dashboard_a)] == ["c1", "c2"]

    def test_empty_layout_yields_nothing(self, superuser):
        assert collect_dashboard_cards(_make_dashboard(superuser)) == []


class TestBuildDashboardDataPayload:
    def test_single_frame_shape(self, dashboard_a, superuser):
        payload = build_dashboard_data_payload(superuser, dashboard_a)
        assert payload["dashboard"] == str(dashboard_a.pk)
        assert isinstance(payload["ts"], int)
        assert payload["errors"] == []
        c1 = next(card for card in payload["cards"] if card["card"] == "c1")
        assert c1["kind"] == "execute"
        assert set(c1["data"]) == {"columns", "rows", "total", "limit"}
        c2 = next(card for card in payload["cards"] if card["card"] == "c2")
        assert c2["kind"] == "aggregate"
        assert "series" in c2["data"]

    def test_fail_closed_card_goes_to_errors_without_breaking_frame(self, dashboard_a, superuser):
        # sum 缺 value_field：aggregate_dataset fail-closed 报错，同帧其余卡片不受影响
        dashboard_a.layout[1]["metric"] = "sum"
        dashboard_a.save(update_fields=["layout"])
        payload = build_dashboard_data_payload(superuser, dashboard_a)
        assert [card["card"] for card in payload["cards"]] == ["c1"]
        assert [error["card"] for error in payload["errors"]] == ["c2"]
        assert payload["errors"][0]["detail"]

    def test_empty_layout_yields_empty_cards_frame(self, superuser):
        payload = build_dashboard_data_payload(superuser, _make_dashboard(superuser))
        assert payload["cards"] == [] and payload["errors"] == []


class TestDashboardDataNotifyConsumer:
    def test_connect_pushes_first_frame(self, dashboard_a, superuser):
        consumer, captured, closed = _make_consumer(get_channel_layer(), superuser, dashboard_a.pk)
        async_to_sync(consumer.connect)()
        assert closed == []
        frames = [item for item in captured if item["action"] == "dashboard_data"]
        assert len(frames) == 1
        assert frames[0]["data"]["dashboard"] == str(dashboard_a.pk)

    def test_anonymous_rejected_4401(self, dashboard_a):
        consumer, captured, closed = _make_consumer(get_channel_layer(), None, dashboard_a.pk)
        async_to_sync(consumer.connect)()
        assert closed == [4401]
        # 先 accept 再 close：否则客户端只会拿到握手 403
        assert [item["action"] for item in captured] == ["accepted"]

    def test_personal_dashboard_rejected_for_other_user(self, superuser, normal_user, dashboard_a):
        dashboard_a.visibility = "personal"
        dashboard_a.save(update_fields=["visibility"])
        consumer, captured, closed = _make_consumer(get_channel_layer(), normal_user, dashboard_a.pk)
        async_to_sync(consumer.connect)()
        assert closed == [4403]
        assert [item["action"] for item in captured] == ["accepted"]
        # 创建者 / 超管可连
        for user in (superuser,):
            owner_consumer, owner_captured, owner_closed = _make_consumer(get_channel_layer(), user, dashboard_a.pk)
            async_to_sync(owner_consumer.connect)()
            assert owner_closed == []
            assert [item["action"] for item in owner_captured] == ["accepted", "dashboard_data"]

    def test_trigger_pushes_frame(self, dashboard_a, superuser):
        consumer, captured, _closed = _make_consumer(get_channel_layer(), superuser, dashboard_a.pk)
        async_to_sync(consumer.connect)()
        # connect 首帧计入合并基准：回拨后触发才会再聚合（否则窗口内被合并）
        consumer._last_built_at = 0.0
        before = len(captured)
        async_to_sync(consumer.dashboard_data_trigger)({"type": "dashboard_data_trigger"})
        frames = [item for item in captured[before:] if item["action"] == "dashboard_data"]
        assert len(frames) == 1
        assert frames[0]["data"]["dashboard"] == str(dashboard_a.pk)

    def test_trigger_silent_when_dashboard_deleted(self, superuser):
        # 已删盘 connect 也进不来（4403），此处仅验证处理器对脏触发的兜底
        consumer, captured, _closed = _make_consumer(get_channel_layer(), superuser, uuid.uuid4())
        async_to_sync(consumer.connect)()
        before = len(captured)
        async_to_sync(consumer.dashboard_data_trigger)({"type": "dashboard_data_trigger"})
        assert len(captured) == before

    def test_trigger_silent_when_visibility_revoked(self, superuser, normal_user, dashboard_a):
        dashboard_a.visibility = "personal"
        dashboard_a.save(update_fields=["visibility"])
        consumer, captured, _closed = _make_consumer(get_channel_layer(), normal_user, dashboard_a.pk)
        # 不走 connect（personal 对该用户会被拒），手动对齐 connect 注入的连接上下文
        consumer.user = normal_user
        consumer.pk = str(dashboard_a.pk)
        async_to_sync(consumer.dashboard_data_trigger)({"type": "dashboard_data_trigger"})
        assert captured == []

    def test_trigger_skips_when_disconnected(self, dashboard_a, superuser):
        consumer, captured, _closed = _make_consumer(get_channel_layer(), superuser, dashboard_a.pk)
        consumer.disconnected = True
        async_to_sync(consumer.dashboard_data_trigger)({"type": "dashboard_data_trigger"})
        assert captured == []

    def test_trigger_merge_window_builds_once(self, dashboard_a, superuser, monkeypatch):
        """合并窗口内背靠背触发只聚合一次；窗口过后同触发照常聚合。"""
        calls = []
        real_build = build_dashboard_data_payload

        def counting_build(user, dashboard_obj):
            calls.append(str(dashboard_obj.pk))
            return real_build(user, dashboard_obj)

        monkeypatch.setattr("dataset.ws_dashboard.build_dashboard_data_payload", counting_build)
        consumer, _captured, _closed = _make_consumer(get_channel_layer(), superuser, dashboard_a.pk)
        async_to_sync(consumer.connect)()
        # connect 首帧即一次构建；其后窗口内的重复触发（beat 背靠背）全部合并
        async_to_sync(consumer.dashboard_data_trigger)({"type": "dashboard_data_trigger"})
        async_to_sync(consumer.dashboard_data_trigger)({"type": "dashboard_data_trigger"})
        assert len(calls) == 1

        # 手动把上次聚合完成时刻拨回窗口之外：周期性数据刷新不受合并影响
        consumer._last_built_at -= TRIGGER_MERGE_WINDOW * 2
        async_to_sync(consumer.dashboard_data_trigger)({"type": "dashboard_data_trigger"})
        assert len(calls) == 2

    def test_trigger_build_failure_degrades_to_error_frame_then_recovers(self, dashboard_a, superuser, monkeypatch):
        consumer, captured, _closed = _make_consumer(get_channel_layer(), superuser, dashboard_a.pk)
        async_to_sync(consumer.connect)()

        def _boom(user, dashboard_obj):
            raise RuntimeError("boom")

        monkeypatch.setattr("dataset.ws_dashboard.build_dashboard_data_payload", _boom)
        consumer._last_built_at = 0.0
        async_to_sync(consumer.dashboard_data_trigger)({"type": "dashboard_data_trigger"})
        frames = [item for item in captured if item["action"] == "dashboard_data"]
        # 构建失败不让连接死：降级为全盘级错误帧（card="*"）
        assert frames[-1]["data"]["cards"] == []
        assert [error["card"] for error in frames[-1]["data"]["errors"]] == ["*"]

        # 失败不推进合并基准：恢复后窗口内的重试仍执行并拿到数据帧
        monkeypatch.setattr("dataset.ws_dashboard.build_dashboard_data_payload", build_dashboard_data_payload)
        async_to_sync(consumer.dashboard_data_trigger)({"type": "dashboard_data_trigger"})
        frames = [item for item in captured if item["action"] == "dashboard_data"]
        assert len(frames) == 3
        assert frames[-1]["data"]["cards"]

    def test_unknown_upstream_action_rejected(self, dashboard_a, superuser):
        consumer, captured, _closed = _make_consumer(get_channel_layer(), superuser, dashboard_a.pk)
        async_to_sync(consumer.connect)()
        before = len(captured)
        async_to_sync(consumer.receive_json)("anything", {}, "")
        reply = captured[before]
        assert reply["action"] == "anything"


class TestGroupBroadcastDashboard:
    def test_group_send_reaches_each_observer(self, dashboard_a, superuser, normal_user):
        """组广播只投递无载荷触发事件：各连接各自聚合、各自自推。"""
        layer = get_channel_layer()
        consumer_a, captured_a, _closed_a = _make_consumer(layer, superuser, dashboard_a.pk)
        consumer_b, captured_b, _closed_b = _make_consumer(layer, normal_user, dashboard_a.pk)
        async_to_sync(consumer_a.connect)()
        async_to_sync(consumer_b.connect)()

        async_to_sync(layer.group_send)(dashboard_group_name(dashboard_a.pk), {"type": "dashboard_data_trigger"})
        for consumer, captured in ((consumer_a, captured_a), (consumer_b, captured_b)):
            event = async_to_sync(layer.receive)(consumer.channel_name)
            assert event == {"type": "dashboard_data_trigger"}  # 无载荷：聚合上下文以连接自身为准
            # connect 首帧计入合并基准：回拨后触发才会再聚合
            consumer._last_built_at = 0.0
            before = len(captured)
            async_to_sync(consumer.dashboard_data_trigger)(event)
            frames = [item for item in captured[before:] if item["action"] == "dashboard_data"]
            assert len(frames) == 1
            assert frames[0]["data"]["dashboard"] == str(dashboard_a.pk)
            assert frames[0]["data"]["errors"] == []


class TestObserverDataIsolation:
    """按观察者聚合的核心安全承诺：同一帧结构，不同数据权限拿到不同数据。"""

    def test_each_observer_gets_own_aggregation(self, dashboard_a, superuser, normal_user):
        from identity.models import UserInfo
        from system.models import DataPermission

        other = UserInfo.objects.create_user(username="observer_other", password="Test@123456")
        normal_user.rules.add(
            DataPermission.objects.create(
                name="observer-self",
                rules=[
                    {
                        "table": "identity.userinfo",
                        "field": "id",
                        "type": "value.user.id",
                        "match": "exact",
                        "value": "",
                        "exclude": False,
                    }
                ],
            )
        )
        other.rules.add(
            DataPermission.objects.create(
                name="observer-all",
                rules=[
                    {
                        "table": "identity.userinfo",
                        "field": "id",
                        "type": "value.all",
                        "match": "all",
                        "value": "",
                        "exclude": False,
                    }
                ],
            )
        )

        layer = get_channel_layer()
        consumer_self, captured_self, _ = _make_consumer(layer, normal_user, dashboard_a.pk)
        consumer_all, captured_all, _ = _make_consumer(layer, other, dashboard_a.pk)
        async_to_sync(consumer_self.connect)()
        async_to_sync(consumer_all.connect)()

        def frame(captured):
            return [item for item in captured if item["action"] == "dashboard_data"][-1]["data"]

        def card_data(payload, card_id):
            return next(card["data"] for card in payload["cards"] if card["card"] == card_id)

        self_frame, all_frame = frame(captured_self), frame(captured_all)
        self_total = card_data(self_frame, "c1")["total"]
        all_total = card_data(all_frame, "c1")["total"]
        # 自限观察者只看到自己一行；全量观察者看到全部（含超管 / 自己）
        assert self_total == 1
        assert all_total == UserInfo.objects.count()
        assert all_total > self_total
        # 分组序列同样按各自行集聚合（不是同一份缓存数据换壳）
        assert [item["name"] for item in card_data(self_frame, "c2")["series"]] == [normal_user.username]
        assert {item["name"] for item in card_data(all_frame, "c2")["series"]} == set(
            UserInfo.objects.values_list("username", flat=True)
        )


class TestPushDashboardDataTask:
    def test_offline_dashboard_not_triggered_and_not_marked(self, dashboard_a, monkeypatch):
        triggered = []
        monkeypatch.setattr(
            "dataset.ws_dashboard.broadcast_dashboard_data_trigger", lambda pk: triggered.append(str(pk))
        )
        assert push_dashboard_data() == 0
        assert triggered == []
        # 离盘不落节流键：观察者上线后能尽快收到首帧
        assert cache.get(dashboard_push_ts_key(dashboard_a.pk)) is None

    def test_online_dashboard_triggered_then_throttled(self, dashboard_a, superuser, monkeypatch):
        layer = get_channel_layer()
        consumer, _captured, _closed = _make_consumer(layer, superuser, dashboard_a.pk)
        async_to_sync(consumer.connect)()
        triggered = []
        monkeypatch.setattr(
            "dataset.ws_dashboard.broadcast_dashboard_data_trigger", lambda pk: triggered.append(str(pk))
        )

        assert push_dashboard_data() == 1
        assert triggered == [str(dashboard_a.pk)]
        assert isinstance(cache.get(dashboard_push_ts_key(dashboard_a.pk)), int)

        # 节流窗口内重复运行：跳过
        assert push_dashboard_data() == 0
        assert triggered == [str(dashboard_a.pk)]

    def test_stale_throttle_key_allows_push(self, dashboard_a, superuser, monkeypatch):
        layer = get_channel_layer()
        consumer, _captured, _closed = _make_consumer(layer, superuser, dashboard_a.pk)
        async_to_sync(consumer.connect)()
        cache.set(dashboard_push_ts_key(dashboard_a.pk), int(time.time()) - 3600, 7200)
        triggered = []
        monkeypatch.setattr(
            "dataset.ws_dashboard.broadcast_dashboard_data_trigger", lambda pk: triggered.append(str(pk))
        )
        assert push_dashboard_data() == 1
        assert triggered == [str(dashboard_a.pk)]

    def test_beat_registration(self):
        from common.celery.decorator import get_register_period_tasks

        entries = [detail for task in get_register_period_tasks() for detail in task.values()]
        match = [detail for detail in entries if detail["task"].endswith("push_dashboard_data")]
        assert len(match) == 1
        assert match[0]["interval"] == 15
        assert match[0]["module"] == "analysis"


class TestPushHelpersDashboard:
    def test_dashboard_push_due_throttle(self, dashboard_a):
        now = int(time.time())
        assert dashboard_push_due(dashboard_a.pk, now=now) is True  # 未推送过
        cache.set(dashboard_push_ts_key(dashboard_a.pk), now - 5, 7200)
        assert dashboard_push_due(dashboard_a.pk, now=now) is False  # 窗口内
        cache.set(dashboard_push_ts_key(dashboard_a.pk), now - MIN_DATA_PUSH_INTERVAL, 7200)
        assert dashboard_push_due(dashboard_a.pk, now=now) is True

    def test_claim_is_atomic_single_winner(self, dashboard_a):
        first = claim_dashboard_push(dashboard_a.pk)
        second = claim_dashboard_push(dashboard_a.pk)
        assert first is True
        assert second is False  # 占位期内后来者落选

    def test_layer_without_get_layers_assumes_online(self):
        assert _group_has_viewers(object(), "any_group") is True
