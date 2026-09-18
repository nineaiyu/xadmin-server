# -*- coding: utf-8 -*-
"""脱敏/审批/表单采集内置示例种子守护测试。

loadjson/datamaskrule.json、approvalflow*.json、dynamicform*.json 是新装环境
的内置示例（load_init_json 按 loaddata upsert 灌入），与数据分析示例
（test_analysis_seed.py）同套纪律。这里锁定五件事：

1. 注册完整：load_init_json 注册了全部示例模型，且实例类模型
   （ApprovalInstance/ApprovalNodeTask/ApprovalRequest）不在其中——它们需要
   「申请人 ≠ 审批人」，种子造不出合规数据，由 seed_demo_flows 命令动态生成，
   误入 loadjson 会在新库（只有首个超管）loaddata 时炸外键；
2. 审计字段安全：creator/modifier 只允许 1 或 null（init_data 新库首个超管），
   禁止引用 admin(pk=2) 等特定环境用户；
3. 脱敏规则指向真实模型字段（model label 可解析、field 存在）；
4. 流程定义自洽：节点引用/顺序/条件表达式合法、form_schema 类型白名单、
   version=1 且 v1 快照与节点定义一致（版本审计可信）；
5. 表单 schema 与提交数据过同一套校验（validate_schema/validate_submission_data）。
"""

import json
import os

import pytest
from django.apps import apps
from django.conf import settings as dj_settings

from system.management.commands.load_init_json import Command as LoadInitJsonCommand
from system.serializers.approval_flow import FORM_FIELD_TYPES
from system.utils.approval_flow import CONDITION_OPS
from system.utils.dform import validate_schema, validate_submission_data

LOADJSON_DIR = os.path.join(dj_settings.PROJECT_DIR, "loadjson")

# 实例类演示数据必须走 seed_demo_flows（申请人 ≠ 审批人，种子无法合规构造）
INSTANCE_MODEL_NAMES = ("approvalinstance", "approvalnodetask", "approvalrequest")
# load_init_json 新增注册的示例模型
SEED_MODEL_NAMES = (
    "datamaskrule",
    "approvalflow",
    "approvalflownode",
    "approvalflowversion",
    "dynamicform",
    "dynamicformsubmission",
)


def _load(name: str):
    with open(os.path.join(LOADJSON_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def _registered_names() -> list:
    return [model._meta.model_name for model in LoadInitJsonCommand.model_names]


def test_seed_models_are_registered():
    """示例模型必须全部注册进 load_init_json（漏注册 = 新库无示例数据）。"""
    registered = _registered_names()
    for name in SEED_MODEL_NAMES:
        assert name in registered, f"{name} 未注册进 load_init_json"
    for name in INSTANCE_MODEL_NAMES:
        assert name not in registered, f"实例类模型 {name} 禁止进入 load_init_json"


def test_seed_files_exist_and_instance_files_absent():
    """注册模型的种子文件必须存在；实例类模型不得有种子文件。"""
    for name in SEED_MODEL_NAMES:
        assert os.path.exists(os.path.join(LOADJSON_DIR, f"{name}.json")), f"缺少 {name}.json"
    for name in INSTANCE_MODEL_NAMES:
        assert not os.path.exists(os.path.join(LOADJSON_DIR, f"{name}.json")), f"{name}.json 不应存在"


def test_seed_fixture_pks_unique_per_model():
    """同一模型的 fixture pk 不得重复：loaddata 对重复 pk 走 UPDATE，后一条会静默覆盖前一条。"""
    seen: dict = {}
    for name in sorted(f for f in os.listdir(LOADJSON_DIR) if f.endswith(".json")):
        for item in _load(name):
            if not isinstance(item, dict) or "model" not in item or "pk" not in item:
                continue
            key = (item["model"], str(item["pk"]))
            assert key not in seen, f"{key} 在 {name} 与 {seen[key]} 中重复（loaddata 会静默覆盖）"
            seen[key] = name


@pytest.mark.parametrize("name", [*SEED_MODEL_NAMES, "dataset", "dashboard", "screen", "report"])
def test_seed_audit_fields_reference_first_superuser_only(name):
    """种子审计字段只允许 creator/modifier = 1 或 null（新库仅有首个超管）。"""
    for item in _load(f"{name}.json"):
        for key in ("creator", "modifier"):
            value = item["fields"].get(key)
            assert value in (1, None), f"{name}.json 的 {key}={value} 在新库不存在，loaddata 会炸外键"


def test_seed_mask_rules_target_real_fields():
    """脱敏规则的 (model, field) 必须指向真实模型字段，否则规则永远不生效。"""
    for item in _load("datamaskrule.json"):
        fields = item["fields"]
        model_class = apps.get_model(fields["model"])
        assert fields["field"] in {f.name for f in model_class._meta.get_fields()}, (
            f"{fields['model']}.{fields['field']} 不是真实字段"
        )
        assert fields["is_active"] is True, "内置示例应处于激活态（演示本意）"


def _snapshot_node_keys():
    return (
        "name",
        "order",
        "approve_type",
        "approve_ratio",
        "assignee_type",
        "assignee_value",
        "condition",
        "routes",
        "layout",
        "timeout_hours",
    )


def test_seed_flow_definitions_consistent():
    """节点引用/顺序/审批人配置/条件表达式合法，且 v1 快照与活定义一致。"""
    flows = {item["pk"]: item for item in _load("approvalflow.json")}
    versions = _load("approvalflowversion.json")

    nodes_by_flow: dict = {}
    for node in _load("approvalflownode.json"):
        fields = node["fields"]
        assert fields["flow"] in flows, f"节点 {node['pk']} 引用了不存在的流程"
        assert fields["name"].strip(), "节点名必填"
        assert fields["assignee_type"] == "leader" or fields["assignee_value"].strip(), (
            "非 leader 节点必须配置审批人（leader 节点的审批人由申请人所在部门动态解析）"
        )
        condition = fields["condition"] or {}
        if condition:
            assert condition.get("field"), "条件表达式必须有 field"
            assert condition.get("op") in CONDITION_OPS, f"条件运算符非法: {condition.get('op')}"
        nodes_by_flow.setdefault(fields["flow"], []).append(fields)

    for flow_pk, flow in flows.items():
        nodes = sorted(nodes_by_flow.get(flow_pk, []), key=lambda item: item["order"])
        assert nodes, f"流程 {flow['fields']['code']} 至少需要一个节点"
        assert [node["order"] for node in nodes] == list(range(1, len(nodes) + 1)), "节点 order 必须从 1 连续递增"
        assert flow["fields"]["version"] == 1, "内置流程应为 v1（初始版本）"

        flow_versions = [item for item in versions if item["fields"]["flow"] == flow_pk]
        assert [item["fields"]["version"] for item in flow_versions] == [1], "v1 快照必须恰好一条"
        snapshot = flow_versions[0]["fields"]["snapshot"]
        assert snapshot["name"] == flow["fields"]["name"]
        assert snapshot["code"] == flow["fields"]["code"]
        assert snapshot["form_schema"] == flow["fields"]["form_schema"]
        assert [dict((key, node[key]) for key in _snapshot_node_keys()) for node in snapshot["nodes"]] == [
            dict((key, node[key]) for key in _snapshot_node_keys()) for node in nodes
        ], "v1 快照必须与活定义一致（版本审计可信）"


def test_seed_flow_form_schema_valid():
    """流程 form_schema：key 唯一、type 白名单、select 必须带 options。"""
    for flow in _load("approvalflow.json"):
        seen = set()
        for item in flow["fields"]["form_schema"]:
            key = (item.get("key") or "").strip()
            assert key and key not in seen, f"form_schema key 非法或重复: {key}"
            seen.add(key)
            field_type = item.get("type") or "text"
            assert field_type in FORM_FIELD_TYPES, f"form_schema type 非法: {field_type}"
            if field_type == "select":
                assert isinstance(item.get("options"), list), f"select 字段 {key} 必须带 options"


def test_seed_dynamic_form_schema_passes_validation():
    """表单定义 schema 必须通过写入侧同一套校验（validate_schema，纯函数无需 db）。"""
    for form in _load("dynamicform.json"):
        validate_schema(form["fields"]["schema"])


def test_seed_submissions_pass_validation():
    """内置提交数据必须能过对应表单 schema 的提交侧校验（引用 + 内容同源）。"""
    forms = {item["pk"]: item for item in _load("dynamicform.json")}
    for submission in _load("dynamicformsubmission.json"):
        fields = submission["fields"]
        form = forms.get(fields["form"])
        assert form is not None, f"提交 {submission['pk']} 引用了不存在的表单"
        validate_submission_data(form["fields"]["schema"], fields["data"])


def test_seed_demo_flows_command_aligns_with_seed():
    """seed_demo_flows 的流程 code / 审批人改写目标必须与种子对齐（防改名静默失效）。"""
    from system.management.commands import seed_demo_flows as command

    codes = {item["fields"]["code"] for item in _load("approvalflow.json")}
    assert set(command.FLOW_CODES) <= codes, "seed_demo_flows.FLOW_CODES 与种子流程 code 不一致"
    # 互审配置：目标审批人列表必须同时包含申请人侧与审批人侧演示用户，
    # 否则其中一人发起申请时全节点候选为空（fail-closed）
    targets = command.DEMO_ASSIGNEE_VALUE.split(",")
    assert command.DEMO_APPLIER in targets and command.DEMO_APPROVER in targets
