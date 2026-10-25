#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""仪表盘（工作台）服务端聚合推送：卡片展开 + 按观察者聚合整盘数据。

与大屏（dataset/screen_data.py）同一推送模型：**按观察者各自计算、各自自推**。
execute/aggregate 的数据权限绑定浏览者（行级 / 字段 / 卡片级三层 fail-closed），
组广播数据帧会跨用户泄露，故服务端只投递「该刷新了」的触发事件
（``dataset.ws_dashboard.dashboard_data_trigger``），各连接收到后以**连接自身
用户**视角聚合本仪表盘的卡片数据并只发给自己——权限语义与「客户端逐卡 HTTP
重拉」逐字节等价，M 卡 × N 观察者的 HTTP 请求收敛为每观察者每轮 1 帧。

卡片集 = 仪表盘 ``layout``（先经 ``filter_layout_for_user`` 做卡片级 allowed_roles
过滤，与 REST 读侧同口径）；取数执行与逐卡容错复用大屏链路的同一内核
（execute_card / load_datasets / error_detail），同帧内相同取数参数只执行一次。
"""

import time
from typing import Any

from dataset.screen_data import (
    KIND_AGGREGATE,
    KIND_EXECUTE,
    CardRef,
    error_detail,
    execute_card,
    load_datasets,
)
from dataset.utils.dataset import filter_layout_for_user


def load_visible_dashboard(user: Any, dashboard_pk: Any) -> Any:
    """数据推送通道准入：仪表盘已删或用户失去可见性返回 None（与 REST 读侧同口径）。

    connect 时已把关一次；运行期共享被收回 / 仪表盘被删是理论不可达的兜底分支。
    """
    from dataset.models.dataset import Dashboard

    if not user or not getattr(user, "is_authenticated", False):
        return None
    dashboard = Dashboard.objects.filter(pk=dashboard_pk).first()
    if dashboard is None:
        return None
    if getattr(user, "is_superuser", False) or dashboard.creator_id == user.pk:
        return dashboard
    return dashboard if dashboard.visibility == "shared" else None


def can_view_dashboard(user: Any, dashboard_pk: Any) -> bool:
    """连接准入判定（与 HTTP _visible_queryset 同口径）：超管 / 创建者 / shared 可见。"""
    return load_visible_dashboard(user, dashboard_pk) is not None


def collect_dashboard_cards(dashboard: Any, user: Any = None) -> list[CardRef]:
    """展开仪表盘的全部卡片定义（纯函数核心：不做任何数据集查询）。

    - ``user`` 传入时先按 ``filter_layout_for_user`` 过滤卡片级权限（allowed_roles，
      与 REST 读侧同口径）；None（纯函数单测 / 全量展开）不过滤；
    - 缺 id / dataset 的卡片渲染端也无法取数，与「卡片损坏」同口径跳过。
    """
    cards = list(dashboard.layout or [])
    if user is not None:
        cards = filter_layout_for_user(cards, user)
    refs: list[CardRef] = []
    seen: set[str] = set()
    dashboard_pk = str(dashboard.pk)
    for card in cards:
        if not isinstance(card, dict):
            continue
        card_id = str(card.get("id") or "")
        dataset_pk = str(card.get("dataset") or "")
        if not card_id or not dataset_pk or card_id in seen:
            continue
        seen.add(card_id)
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


def build_dashboard_data_payload(user: Any, dashboard: Any) -> dict[str, Any]:
    """以浏览者视角聚合整盘数据帧（逐卡执行，异常逐卡捕获不中断整帧）。

    引用为空时返回空 cards 帧（无可推数据，前端维持空态）。
    """
    refs = collect_dashboard_cards(dashboard, user=user)
    datasets = load_datasets(refs)
    # 同帧内相同取数参数（同数据集 + 同分组/度量/分桶）只执行一次（与大屏同口径）
    result_cache: dict[tuple[str, ...], dict[str, Any]] = {}
    cards: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for ref in refs:
        cache_key = (
            ref["dataset"],
            ref["kind"],
            ref["chart_type"],
            ref["group_by"],
            ref["metric"],
            ref["date_trunc"],
            ref["value_field"],
        )
        try:
            if cache_key in result_cache:
                data = result_cache[cache_key]
            else:
                data = execute_card(ref, user, datasets.get(ref["dataset"]))
                result_cache[cache_key] = data
            cards.append({"card": ref["card"], "kind": ref["kind"], "data": data})
        except Exception as exc:  # noqa: BLE001 单卡失败只记 errors，不中断整帧（旧链路单卡 4xx 同语义）
            errors.append({"card": ref["card"], "detail": error_detail(exc)})
    return {
        "dashboard": str(dashboard.pk),
        "cards": cards,
        "errors": errors,
        "ts": int(time.time()),
    }
