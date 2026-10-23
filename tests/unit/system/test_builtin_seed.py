# -*- coding: utf-8 -*-
"""脱敏/审批/表单采集内置示例种子守护测试。

种子已按应用分层：脱敏规则在 ``audit/seeds/``，流程定义在 ``approval/seeds/``，
表单与数据分析示例在 ``dataset/seeds/sample/``（默认不装载，``init_data`` 与
``load_init_json --with-sample`` 才装）。与 ``test_analysis_seed.py`` 同套纪律，
这里锁定五件事：

1. 装载面完整：上述模型全部出现在装载计划中（契约层）或示例层，且实例类模型
   （ApprovalInstance/ApprovalNodeTask/ApprovalRequest）不在其中——它们需要
   「申请人 ≠ 审批人」，种子造不出合规数据，由 seed_demo_flows 命令动态生成，
   误入种子会在新库（只有首个超管）loaddata 时炸外键；
2. 审计字段安全：creator/modifier 只允许 1 或 null（init_data 新库首个超管），
   禁止引用 admin(pk=2) 等特定环境用户；
3. 脱敏规则指向真实模型字段（model label 可解析、field 存在）；
4. 流程定义自洽：节点引用/顺序/条件表达式合法、form_schema 类型白名单、
   version=1 且 v1 快照与节点定义一致（版本审计可信）；
5. 表单 schema 与提交数据过同一套校验（validate_schema/validate_submission_data）。
"""

import json
from io import StringIO

import pytest
from django.apps import apps
from django.core.management import call_command

from approval.serializers.approval_flow import FORM_FIELD_TYPES
from approval.services.approval_flow import CONDITION_OPS
from dataset.utils.dform import validate_schema, validate_submission_data
from identity.builtin import BUILTIN_ROLES
from identity.models import UserRole
from system.models import FieldPermission, Menu, ModelLabelField
from system.services.seeding import build_plan, discover, read_model_rows

#: 文件 stem → 模型 label（种子按应用分层后，读取一律走聚合入口）
SEED_MODELS = {
    "datamaskrule": "audit.datamaskrule",
    "approvalflow": "approval.approvalflow",
    "approvalflownode": "approval.approvalflownode",
    "approvalflowversion": "approval.approvalflowversion",
    "dynamicform": "dataset.dynamicform",
    "dynamicformsubmission": "dataset.dynamicformsubmission",
    "dataset": "dataset.dataset",
    "dashboard": "dataset.dashboard",
    "screen": "dataset.screen",
    "report": "dataset.report",
}

# 实例类演示数据必须走 seed_demo_flows（申请人 ≠ 审批人，种子无法合规构造）
INSTANCE_MODEL_NAMES = ("approvalinstance", "approvalnodetask", "approvalrequest")
# 内置示例 / 契约模型的文件 stem（装载面必须覆盖）
SEED_MODEL_NAMES = (
    "datamaskrule",
    "approvalflow",
    "approvalflownode",
    "approvalflowversion",
    "dynamicform",
    "dynamicformsubmission",
)


def _load(name: str):
    return read_model_rows(SEED_MODELS[name], include_sample=True)


def _plan_specs():
    plan = build_plan(discover(), include_sample=True)
    return [*plan.all_specs(), *plan.samples]


def test_seed_models_are_in_load_plan():
    """示例模型必须出现在装载计划（契约层或示例层；漏登记 = 新库无示例数据）。"""
    planned = {spec.model for spec in _plan_specs()}
    for name in SEED_MODEL_NAMES:
        assert SEED_MODELS[name] in planned, f"{SEED_MODELS[name]} 未出现在装载计划中"
    for name in INSTANCE_MODEL_NAMES:
        assert not any(model.endswith(f".{name}") for model in planned), (
            f"实例类模型 {name} 禁止进入种子（由 seed_demo_flows 动态生成）"
        )


def test_seed_rows_exist_and_instance_files_absent():
    """注册模型的种子行必须存在；实例类模型不得有种子行。"""
    for name in SEED_MODEL_NAMES:
        assert _load(name), f"缺少 {name} 的种子行"
    instance_models = {f"approval.{name}" for name in INSTANCE_MODEL_NAMES}
    assert not instance_models & {spec.model for spec in _plan_specs()}


def test_seed_fixture_pks_unique_per_model():
    """同一模型的主键不得重复：loaddata 对重复 pk 走 UPDATE，后一条会静默覆盖前一条。"""
    seen: dict = {}
    for spec in _plan_specs():
        for item in json.loads(spec.path.read_text(encoding="utf-8")):
            key = (item.get("model"), str(item.get("pk")))
            assert key not in seen, f"{key} 在 {spec.path} 与 {seen[key]} 中重复（loaddata 会静默覆盖）"
            seen[key] = str(spec.path)


@pytest.mark.parametrize("name", [*SEED_MODEL_NAMES, "dataset", "dashboard", "screen", "report"])
def test_seed_audit_fields_reference_first_superuser_only(name):
    """种子审计字段只允许 creator/modifier = 1 或 null（新库仅有首个超管）。"""
    for item in _load(name):
        for key in ("creator", "modifier"):
            value = item["fields"].get(key)
            assert value in (1, None), f"{name}.json 的 {key}={value} 在新库不存在，loaddata 会炸外键"


def test_seed_mask_rules_target_real_fields():
    """脱敏规则的 (model, field) 必须指向真实模型字段，否则规则永远不生效。"""
    for item in _load("datamaskrule"):
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
    flows = {item["pk"]: item for item in _load("approvalflow")}
    versions = _load("approvalflowversion")

    nodes_by_flow: dict = {}
    for node in _load("approvalflownode"):
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
    for flow in _load("approvalflow"):
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
    for form in _load("dynamicform"):
        validate_schema(form["fields"]["schema"])


def test_seed_submissions_pass_validation():
    """内置提交数据必须能过对应表单 schema 的提交侧校验（引用 + 内容同源）。"""
    forms = {item["pk"]: item for item in _load("dynamicform")}
    for submission in _load("dynamicformsubmission"):
        fields = submission["fields"]
        form = forms.get(fields["form"])
        assert form is not None, f"提交 {submission['pk']} 引用了不存在的表单"
        validate_submission_data(form["fields"]["schema"], fields["data"])


def test_seed_demo_flows_command_aligns_with_seed():
    """seed_demo_flows 的流程 code / 审批人改写目标必须与种子对齐（防改名静默失效）。"""
    from demo_seed.management.commands import seed_demo_flows as command

    codes = {item["fields"]["code"] for item in _load("approvalflow")}
    assert set(command.FLOW_CODES) <= codes, "seed_demo_flows.FLOW_CODES 与种子流程 code 不一致"
    # 互审配置：目标审批人列表必须同时包含申请人侧与审批人侧演示用户，
    # 否则其中一人发起申请时全节点候选为空（fail-closed）
    targets = command.DEMO_ASSIGNEE_VALUE.split(",")
    assert command.DEMO_APPLIER in targets and command.DEMO_APPROVER in targets


class TestSeedOrderingFillsBuiltinRoles:
    """种子时序守护：内置角色的菜单/字段白名单依赖本命令种入的数据。

    migrate 的 post_migrate 同步先于 load_init_json 执行，彼时 Menu 表与
    ModelLabelField 字段树均为空，内置角色只能空挂（字段权限 fail-closed，
    缺白名单 = 非超管接口输出空对象）。2026-10-01 E2E 真库种子实证该缺口：
    SystemAdmin 0 菜单、DeptManager 0 菜单 0 白名单（任命装配后
    用户列表被裁剪成空对象）。本类锁定「load_init_json 收尾必须补齐」。
    """

    @pytest.mark.django_db
    def test_load_init_json_fills_builtin_role_menus_and_fields(self, seed_creator_user):
        spec = {s["code"]: s for s in BUILTIN_ROLES}

        # 前提（缺口态）：测试建库时 post_migrate 已建内置角色，但彼时菜单/字段树为空
        assert UserRole.objects.filter(code="SystemAdmin", builtin=True).exists()
        assert UserRole.objects.get(code="SystemAdmin").menu.count() == 0
        assert FieldPermission.objects.count() == 0

        out = StringIO()
        call_command("load_init_json", verbosity=0, stdout=out)
        assert "[内置角色]" in out.getvalue(), "load_init_json 收尾必须补跑内置角色同步"

        # SystemAdmin：全部活跃菜单（grant_all_menus）
        active_menus = Menu.objects.filter(is_active=True).count()
        assert active_menus > 0, "菜单种子未入库"
        assert UserRole.objects.get(code="SystemAdmin").menu.count() == active_menus

        # DeptManager：固定权限点清单 + userinfo/deptinfo 字段白名单（每菜单一份全字段）
        manager_spec = spec["DeptManager"]
        manager = UserRole.objects.get(code="DeptManager")
        assert set(manager.menu.values_list("name", flat=True)) == set(manager_spec["menu_names"])
        for model_name in manager_spec["field_models"]:
            assert ModelLabelField.objects.filter(name=model_name, parent__isnull=True).exists(), (
                f"字段树缺 {model_name} 根节点"
            )
        field_perms = FieldPermission.objects.filter(role=manager)
        assert field_perms.count() == manager.menu.count() > 0
        assert all(fp.field.count() > 0 for fp in field_perms), "字段白名单不得为空行（fail-closed 裁空）"

        # 幂等：重复导入不重复建行（get_or_create / menu.set 对齐语义）
        before_menu = manager.menu.count()
        before_fp = field_perms.count()
        call_command("load_init_json", verbosity=0)
        assert UserRole.objects.get(code="DeptManager").menu.count() == before_menu
        assert FieldPermission.objects.filter(role=manager).count() == before_fp

    @pytest.mark.django_db
    def test_load_init_json_fills_non_builtin_role_field_gaps(self, seed_creator_user):
        """非内置角色（演示角色、二开新建角色）的字段白名单缺口同样由收尾补齐。

        内置角色由 sync_builtin_roles 静态配置覆盖，而非内置角色的白名单不在种子里：
        种子按 pk 重灌会覆盖运行期补齐的关系，字段权限 fail-closed 会让这些角色在
        对应接口输出空对象（升级后「演示账号审批列表空白」即由此产生）。
        """
        from system.management.commands.load_init_json import Command
        from system.services.permission_sync import audit_field_permissions

        call_command("load_init_json", verbosity=0)
        menu = (
            Menu.objects.filter(menu_type=Menu.MenuChoices.PERMISSION, is_active=True)
            .filter(model__isnull=False)
            .distinct()
            .first()
        )
        assert menu is not None, "前置：种子应提供已绑定模型的权限点"

        role = UserRole.objects.create(name="自愈守护角色", is_active=True)
        role.menu.add(menu)
        assert audit_field_permissions(roles=[role]), "前置：获权限点但无白名单的角色应判为缺口"

        out = StringIO()
        command = Command(stdout=out)
        command._fill_field_permissions_after_seed()
        assert audit_field_permissions(roles=[role]) == [], "收尾应补齐非内置角色的字段白名单"
        assert "[字段权限]" in out.getvalue(), "收尾补齐应有可见日志（便于运维确认）"

        # 幂等且不覆盖人工裁剪：管理员在角色页收敛到单个字段后，重复收尾保持收敛结果
        field_permission = FieldPermission.objects.filter(role=role, menu=menu).first()
        assert field_permission is not None and field_permission.field.exists()
        kept = field_permission.field.first()
        field_permission.field.set([kept])
        command._fill_field_permissions_after_seed()
        field_permission.refresh_from_db()
        assert list(field_permission.field.values_list("pk", flat=True)) == [kept.pk], "人工裁剪不应被改写"
