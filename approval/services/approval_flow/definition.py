#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""流程定义保存编排：创建 / 改版更新 / 版本回滚（事务边界在此收口）。

序列化层只保留字段校验与展示派生；「主表保存 + 节点版本化 + 并发防护」的
事务编排收敛到本模块，与引擎其余部分（conditions / engine / versioning）
同层。三类写路径共用版本化语义：定义（节点或表单）有实质变化才落新版本，
在途实例按自身 ``flow_version`` 推进，不受定义变更影响。
"""

import json
from typing import Any

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from approval.models.approval import ApprovalFlow, ApprovalFlowNode
from approval.services.approval_flow.versioning import apply_definition, build_snapshot


def form_schema_locked(flow: Any) -> bool:
    """是否被 dform 绑定（绑定期 form_schema 由表单侧单向投影维护）。"""
    return bool(flow.bound_forms.filter(is_template=False).exists())


@transaction.atomic  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def create_flow(validated_data: Any) -> Any:
    """创建流程定义：主表落行 + 节点初始版本（同一事务）。"""
    nodes = validated_data.pop("nodes", [])
    flow = ApprovalFlow.objects.create(**validated_data)
    apply_definition(flow, nodes, remark=_("Initial version"))
    return flow


@transaction.atomic  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def update_flow(instance: Any, validated_data: Any) -> Any:
    """改版更新：编辑锁丢弃 / 行锁串行化 / 乐观锁基线比对 / 定义实质变化才落新版本。"""
    nodes = validated_data.pop("nodes", None)
    base_updated_time = validated_data.pop("base_updated_time", None)
    # 编辑锁：绑定期 form_schema 由绑定表单单向投影，客户端改动忽略不落库
    # （响应带 form_schema_locked=true 供前端禁用编辑；防手滑覆盖投影结果）
    if "form_schema" in validated_data and form_schema_locked(instance):
        validated_data.pop("form_schema")
    # 行锁：并发改版时「版本号分配 + 旧行收口 + 新行落库」串行化
    # （版本快照的 (flow, version) 唯一约束仍作二层兜底）
    flow = ApprovalFlow.objects.select_for_update().get(pk=instance.pk)
    check_not_concurrently_modified(flow, base_updated_time)
    for attr, value in validated_data.items():
        setattr(flow, attr, value)
    flow.save()
    # 定义（节点或表单）有实质变化才落新版本；无变化时节点行保持原样（主键不变）
    if nodes is not None and definition_changed(flow, nodes):
        apply_definition(flow, nodes, remark=_("Nodes updated"))
    return flow


def rollback_flow(flow: Any, version: int, remark: Any = "") -> tuple[bool, Any]:
    """回滚到历史版本：快照写入活定义（节点/表单）并落新版本。返回 (ok, detail)。

    与改节点同口径：在途实例按自身 ``flow_version`` 推进，回滚只影响之后发起的新单，
    因此不再受 PENDING 实例限制。表单锁同口径（update 会丢弃客户端表单）：
    绑定期 form_schema 由绑定表单纯向投影维护，回滚不回写历史表单——否则投影
    被旧快照破坏、新单取到与表单不一致的字段定义；此时仅回滚节点，返回提示。
    """
    snapshot = flow.versions.filter(version=version).values_list("snapshot", flat=True).first()
    if snapshot is None:
        return False, str(_("The flow version does not exist"))
    detail = None
    with transaction.atomic():
        flow = ApprovalFlow.objects.select_for_update().get(pk=flow.pk)
        if form_schema_locked(flow):
            detail = str(_("Form schema is locked by a bound form; only nodes were rolled back"))
        else:
            flow.form_schema = snapshot.get("form_schema") or []
            flow.save(update_fields=["form_schema", "updated_time"])
        apply_definition(
            flow,
            snapshot.get("nodes") or [],
            remark=remark or str(_("Rollback from version {}").format(version)),
        )
    return True, detail


def check_not_concurrently_modified(flow: Any, base_updated_time: Any) -> None:
    """乐观锁：编辑基线落后于当前行说明他人已先保存，拒绝整单覆盖（fail-closed）。

    基线值是客户端原样回传的 updated_time 渲染串，这里用同款 DateTimeField 渲染
    当前行再比较，避免日期解析/时区/精度（DATETIME_FORMAT 为秒级）比对歧义；
    未携带基线的历史客户端保持原有语义（不做拦截）。
    """
    if not base_updated_time:
        return
    current = serializers.DateTimeField().to_representation(flow.updated_time)
    if str(base_updated_time) != str(current):
        raise serializers.ValidationError(_("The flow was modified by someone else, please refresh and retry"))


def definition_changed(flow: Any, nodes: Any) -> bool:
    """与最新版本快照比较：nodes 或 form_schema 有变化返回 True。

    历史快照缺 `timeout_action`（字段后补）时按默认值回填再比较，避免
    语义未变的存量定义被误判为「有变化」而多落一个版本。
    """
    latest = flow.versions.order_by("-version").values_list("snapshot", flat=True).first()
    if latest is None:
        return True
    latest = json.loads(json.dumps(latest))
    for node in latest.get("nodes") or []:
        if isinstance(node, dict):
            node.setdefault("timeout_action", ApprovalFlowNode.TimeoutAction.NONE)
    return json.dumps(latest, sort_keys=True, ensure_ascii=False) != json.dumps(
        build_snapshot(flow, nodes), sort_keys=True, ensure_ascii=False
    )
