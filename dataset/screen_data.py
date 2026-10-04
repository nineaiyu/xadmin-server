#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""大屏服务端聚合推送：卡片展开 + 按观察者聚合整屏数据。

推送模型 = **按观察者各自计算、各自自推**：execute/aggregate 的数据权限绑定
浏览者（行级 / 字段 / 卡片级三层 fail-closed），组广播数据帧会跨用户泄露，
故服务端只投递「该刷新了」的触发事件（``dataset.ws_screen.screen_data_trigger``），
各展示连接收到后以**连接自身用户**视角跑本模块聚合，结果只发给自己。
权限语义与旧「展示端逐卡 HTTP 重拉」逐字节等价，M 卡 × N 观察者的 HTTP 请求
收敛为每观察者每轮 1 帧。

帧形态（与展示端渲染模式对齐）：

- canvas（``Screen.layout`` 非空）：单帧，``dashboard=None``，卡片 = 各窗格
  引用仪表盘的卡片并集；
- carousel（``layout`` 空）：按展示连接上报的当前页（``screen_page_state``）
  只聚合该页一帧——展示端只应用当前页帧，全页聚合是 (N-1)/N 查询白跑
  ；未上报/越界回退全页帧（旧调用方与兜底语义不变）。
"""

import time
from typing import Any, TypedDict

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from dataset.utils.dataset import filter_layout_for_user

#: 卡片种类：number 卡读 execute 的 total，其余图表卡走 aggregate 的 series
#: （与前端 ChartCard 的取数分支同口径，见 xadmin-client ChartCard.vue loadData）
KIND_EXECUTE = "execute"
KIND_AGGREGATE = "aggregate"


class CardRef(TypedDict):
    """展开后的卡片定义（collect_screen_cards 的输出，build 的输入）。"""

    card: str
    dashboard: str
    dataset: str
    chart_type: str
    kind: str
    group_by: str
    metric: str
    date_trunc: str
    value_field: str


def load_visible_screen(user, screen_pk):
    """展示连接的数据推送准入：屏已删或用户失去可见性返回 None（与 can_view_screen 同口径）。

    connect 时已把关一次；运行期共享被收回 / 屏被删是理论不可达的兜底分支。
    """
    from dataset.models.dataset import Screen

    if not user or not getattr(user, "is_authenticated", False):
        return None
    screen = Screen.objects.filter(pk=screen_pk).first()
    if screen is None:
        return None
    if getattr(user, "is_superuser", False) or screen.creator_id == user.pk:
        return screen
    return screen if screen.visibility == "shared" else None


def collect_screen_cards(screen, user=None) -> list[CardRef]:
    """展开大屏引用的全部卡片定义（纯函数核心：不做任何数据集查询）。

    - canvas：``layout`` 内 type=dashboard 的窗格按列表序展开；type=metric 的
      指标卡窗格直接合成单卡引用（无所属仪表盘，``dashboard`` 置空串）；
    - carousel：``dashboards`` 清单按页序展开（服务端按该原序计页）；
    - ``user`` 传入时按 ``filter_layout_for_user`` 过滤卡片级权限（allowed_roles，
      与仪表盘读取侧同口径）；None（纯函数单测 / 全量展开）不过滤；
    - 同一 (dashboard, card) 重复出现（同仪表盘多窗格 / 清单重复项）只保留首个：
      数据一致，无需重复查询；
    - 引用的仪表盘已删：静默跳过（展示端本来也渲染不出该仪表盘）。
    """
    from dataset.models.dataset import Dashboard

    dashboard_pks: list[str] = []
    metric_refs: list[CardRef] = []
    if screen.layout:
        for pane in screen.layout:
            if not isinstance(pane, dict):
                continue
            if pane.get("type", "dashboard") == "dashboard" and pane.get("dashboard"):
                dashboard_pks.append(str(pane["dashboard"]))
            elif pane.get("type") == "metric" and pane.get("dataset"):
                # 指标卡窗格：无所属仪表盘，card id 即窗格 pk（展示端以同 id 合成卡接收）
                metric_refs.append(
                    CardRef(
                        card=str(pane["pk"]),
                        dashboard="",
                        dataset=str(pane["dataset"]),
                        chart_type="metric",
                        kind=KIND_AGGREGATE,
                        group_by="",
                        metric=str(pane.get("metric") or "count"),
                        date_trunc="",
                        value_field=str(pane.get("value_field") or ""),
                    )
                )
    else:
        dashboard_pks = [str(item) for item in (screen.dashboards or [])]

    # in_bulk 的键是字段原值（UUID），统一 str(pk) 自建映射，避免类型不一致漏查
    dashboards = {str(item.pk): item for item in Dashboard.objects.filter(pk__in=dashboard_pks)}

    refs: list[CardRef] = list(metric_refs)
    seen: set[tuple[str, str]] = {("", ref["card"]) for ref in metric_refs}
    for dashboard_pk in dashboard_pks:
        dashboard = dashboards.get(dashboard_pk)
        if dashboard is None:
            continue
        cards = list(dashboard.layout or [])
        if user is not None:
            cards = filter_layout_for_user(cards, user)
        for card in cards:
            if not isinstance(card, dict):
                continue
            card_id = str(card.get("id") or "")
            dataset_pk = str(card.get("dataset") or "")
            # 缺 id / dataset 的卡片渲染端也无法取数，与「仪表盘已删」同口径跳过
            if not card_id or not dataset_pk or (dashboard_pk, card_id) in seen:
                continue
            seen.add((dashboard_pk, card_id))
            chart_type = str(card.get("chart_type") or "number")
            refs.append(
                CardRef(
                    card=card_id,
                    dashboard=dashboard_pk,
                    dataset=dataset_pk,
                    chart_type=chart_type,
                    kind=KIND_EXECUTE if chart_type == "number" else KIND_AGGREGATE,
                    group_by=str(card.get("group_by") or ""),
                    metric=str(card.get("metric") or ""),
                    date_trunc=str(card.get("date_trunc") or ""),
                    value_field=str(card.get("value_field") or ""),
                )
            )
    return refs


def build_screen_data_payload(user, screen, rev: int, page_index: int | None = None) -> list[dict[str, Any]]:
    """以浏览者视角聚合整屏数据帧（逐卡执行，异常逐卡捕获不中断整帧）。

    返回帧列表：canvas 单帧；carousel 按 ``page_index`` 只聚合当前页一帧（展示
    连接上报所在页，避免每轮为其余页做白跑查询——(N-1)/N 查询优化），
    ``page_index`` 为 None（旧调用方/未上报页码）或越界时回退逐仪表盘全页帧；
    两种形态的引用都为空时返回空列表（无可推数据，展示端维持空态）。
    """
    refs = collect_screen_cards(screen, user=user)
    ts = int(time.time())
    if screen.layout:
        groups: list[tuple[str | None, list[CardRef]]] = [(None, refs)]
    else:
        # carousel：按首现顺序分仪表盘成帧（dedupe 保序，同清单重复项只推一次）；
        # page_index 命中时只聚合该页（越界/None 回退全页，fail-open 不丢数据）
        ordered = list(dict.fromkeys(ref["dashboard"] for ref in refs))
        groups = [(dashboard_pk, [ref for ref in refs if ref["dashboard"] == dashboard_pk]) for dashboard_pk in ordered]
        if page_index is not None and 0 <= int(page_index) < len(ordered):
            current_pk = ordered[int(page_index)]
            groups = [(current_pk, [ref for ref in refs if ref["dashboard"] == current_pk])]

    payloads: list[dict[str, Any]] = []
    for dashboard_pk, group_refs in groups:
        cards: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for ref in group_refs:
            try:
                cards.append({"card": ref["card"], "kind": ref["kind"], "data": _execute_card(ref, user)})
            except Exception as exc:  # noqa: BLE001 单卡失败只记 errors，不中断整帧（旧链路单卡 4xx 同语义）
                errors.append({"card": ref["card"], "detail": _error_detail(exc)})
        payloads.append(
            {
                "screen": str(screen.pk),
                "dashboard": dashboard_pk,
                "rev": int(rev),
                "cards": cards,
                "errors": errors,
                "ts": ts,
            }
        )
    return payloads


def _execute_card(ref: CardRef, user) -> dict:
    """执行单卡：按 kind 路由到 execute/aggregate（权限过滤在 dataset_query 内 fail-closed）。"""
    from dataset.models.dataset import Dataset
    from dataset.utils.dataset import aggregate_dataset, execute_dataset

    dataset = Dataset.objects.filter(pk=ref["dataset"]).first()
    if dataset is None:
        # 数据集在建卡后被删：旧链路里该卡 HTTP 404 报错，此处进 errors 同语义
        raise ValidationError(_("Unknown dataset in layout"))
    # 数据集定义可见性与旧「逐卡 HTTP 重拉」对齐：REST 路径经 get_object 过滤
    # （personal 数据集对非创建者 404），聚合路径同样 fail-closed 进 errors
    if not (
        getattr(user, "is_superuser", False)
        or dataset.visibility == "shared"
        or dataset.creator_id == getattr(user, "pk", None)
    ):
        raise ValidationError(_("No permission for dataset: {}").format(dataset.name))
    if ref["kind"] == KIND_EXECUTE:
        # 数字卡只读 total：count_only 跳过全量行物化（与 ChartCard 同口径）
        return execute_dataset(dataset, user, count_only=True)
    # date_trunc 仅折线卡下发（前端 ChartCard 同口径：其余图表忽略趋势分桶，
    # 折线未存值时缺省 day）
    date_trunc = (ref["date_trunc"] or "day") if ref["chart_type"] == "line" else ""
    return aggregate_dataset(
        dataset,
        user,
        group_by=ref["group_by"] or None,
        metric=ref["metric"] or "count",
        date_trunc=date_trunc or None,
        value_field=ref["value_field"] or None,
    )


def _error_detail(exc: Exception) -> str:
    """单卡失败的可读原因：ValidationError 取 messages（与 REST execute/aggregate 出口同款拼接）。"""
    if isinstance(exc, ValidationError):
        return "; ".join(exc.messages)
    return str(exc) or exc.__class__.__name__
