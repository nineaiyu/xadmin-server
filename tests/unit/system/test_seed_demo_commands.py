# -*- coding: utf-8 -*-
"""演示数据命令族（seed_demo_all / seed_demo_clean / seed_demo_content）守护。

覆盖四件事：
1. 内容命令幂等（重复执行不重复）且 --clean-only 彻底清理；
2. 一键加载 → 一键卸载往返后演示数据清零（组织 / 审批 / 请假 / 内容 / 用户）；
3. 卸载会回滚对内置种子的改写（流程节点审批人 + 演示部门负责人 + 演示版本快照）；
4. seed_demo_users --clean-only 只清理批量演示用户，不误删命令专用账号。
"""

import json
import os

import pytest
from django.conf import settings
from django.core.management import call_command

from message.models import ChatMessage
from notifications.models import MessageContent
from system.management.commands.seed_demo_flows import INSTANCE_PKS as FLOW_INSTANCE_PKS
from system.management.commands.seed_demo_leave import LEAVE_PKS
from system.models import (
    DeptInfo,
    DynamicForm,
    DynamicFormSubmission,
    Leave,
    Menu,
    MenuMeta,
    ModelLabelField,
    UploadFile,
    UserInfo,
)
from system.models.ai import AiKnowledgeDocument
from system.models.approval import (
    ApprovalDelegation,
    ApprovalFlow,
    ApprovalFlowNode,
    ApprovalFlowVersion,
    ApprovalInstance,
    ApprovalRequest,
)
from system.models.token import ApiApplication
from system.models.webhook import WebhookDelivery, WebhookSubscription

pytestmark = pytest.mark.django_db

PAGE_PATHS = [
    "/form-collection/my/index",
    "/system/approval/instance/index",
    "/system/approval/index",
    "/user/notice/index",
    "/system/leave/index",
]
GRANT_MODELS = [
    "system.approvalflow",
    "system.approvalinstance",
    "system.approvalrequest",
    "system.dynamicform",
    "system.dynamicformsubmission",
    "system.leave",
]


def _load_seed(name):
    with open(os.path.join(settings.PROJECT_DIR, "loadjson", name), encoding="utf-8") as fp:
        return json.load(fp)


@pytest.fixture
def admin(db):
    return UserInfo.objects.create_superuser("xadmin", "xadmin@test.local", "TestPwd123!")


@pytest.fixture
def menus(db):
    for path in PAGE_PATHS:
        meta = MenuMeta.objects.create(title=path)
        page = Menu.objects.create(name=path, path=path, menu_type=Menu.MenuChoices.MENU, meta=meta)
        Menu.objects.create(
            name=f"perm{path}",
            path=f"api{path}$",
            menu_type=Menu.MenuChoices.PERMISSION,
            parent=page,
            method="GET",
            meta=MenuMeta.objects.create(title=f"perm{path}"),
        )


@pytest.fixture
def field_trees(db):
    for model_name in GRANT_MODELS:
        root = ModelLabelField.objects.create(name=model_name, label=model_name, field_type=0)
        ModelLabelField.objects.create(name="id", label="ID", parent=root, field_type=1)


@pytest.fixture
def builtin_flows(db):
    """按 loadjson 真实种子建内置流程与节点（demo_leave / leave / demo_expense 等）。"""
    flows = {}
    for row in _load_seed("approvalflow.json"):
        fields = row["fields"]
        flows[row["pk"]] = ApprovalFlow.objects.create(
            pk=row["pk"],
            code=fields["code"],
            name=fields["name"],
            form_schema=fields.get("form_schema") or [],
            version=fields.get("version", 1),
            is_active=fields.get("is_active", True),
        )
    for row in _load_seed("approvalflownode.json"):
        fields = row["fields"]
        ApprovalFlowNode.objects.create(
            pk=row["pk"],
            flow_id=fields["flow"],
            name=fields["name"],
            order=fields["order"],
            approve_type=fields.get("approve_type", "OR"),
            approve_ratio=fields.get("approve_ratio", 100),
            assignee_type=fields.get("assignee_type", "role"),
            assignee_value=fields.get("assignee_value") or "",
            condition=fields.get("condition") or {},
            routes=fields.get("routes") or [],
            layout=fields.get("layout") or {},
            timeout_hours=fields.get("timeout_hours", 0),
        )
    return flows


@pytest.fixture
def demo_forms(db):
    """按 loadjson 真实种子建示例表单定义（demo flows 的提交数据依赖）。"""
    for row in _load_seed("dynamicform.json"):
        fields = row["fields"]
        DynamicForm.objects.create(
            pk=row["pk"],
            name=fields["name"],
            schema=fields["schema"],
            is_active=fields.get("is_active", True),
            approval_required=fields.get("approval_required", False),
        )


# ---------------------------------------------------------------- 内容命令


def test_content_seed_is_idempotent_and_clean_removes(admin):
    call_command("seed_demo_content")

    assert MessageContent.objects.filter(title__startswith="演示：").count() == 2
    assert ChatMessage.objects.filter(client_msg_id__startswith="demo-content-").count() == 2
    assert AiKnowledgeDocument.objects.filter(path__startswith="upload/演示-").count() == 2
    assert UploadFile.all_objects.filter(filename__startswith="演示-").count() == 2
    assert WebhookSubscription.objects.filter(name="演示-审批事件订阅").count() == 1
    assert WebhookDelivery.objects.count() == 2
    assert ApiApplication.objects.filter(name="演示-开放平台应用").count() == 1

    # 幂等：重复执行不产生重复数据
    call_command("seed_demo_content")
    assert MessageContent.objects.filter(title__startswith="演示：").count() == 2
    assert ChatMessage.objects.filter(client_msg_id__startswith="demo-content-").count() == 2
    assert AiKnowledgeDocument.objects.filter(path__startswith="upload/演示-").count() == 2
    assert UploadFile.all_objects.filter(filename__startswith="演示-").count() == 2
    assert ApiApplication.objects.filter(name="演示-开放平台应用").count() == 1

    call_command("seed_demo_content", clean_only=True)
    assert MessageContent.objects.filter(title__startswith="演示：").count() == 0
    assert ChatMessage.objects.filter(client_msg_id__startswith="demo-content-").count() == 0
    assert AiKnowledgeDocument.objects.filter(path__startswith="upload/演示-").count() == 0
    assert UploadFile.all_objects.filter(filename__startswith="演示-").count() == 0
    assert WebhookSubscription.objects.filter(name="演示-审批事件订阅").count() == 0
    assert WebhookDelivery.objects.count() == 0
    assert ApiApplication.objects.filter(name="演示-开放平台应用").count() == 0


def test_content_seed_without_superuser_is_safe(db):
    """无超管（命令早退）时不创建任何数据，也不抛错。"""
    call_command("seed_demo_content")
    assert MessageContent.objects.filter(title__startswith="演示：").count() == 0


# ---------------------------------------------------------------- 一键加载 / 卸载


def test_all_and_clean_roundtrip(admin, menus, field_trees, builtin_flows, demo_forms):
    call_command("seed_demo_all", skip_users=True)

    # 组织 + 内容 + 审批实例 + 请假全部就位
    assert DeptInfo.objects.filter(code__in=["demo_rd", "demo_fin"]).count() == 2
    assert UserInfo.objects.filter(username="demo_lead").exists()
    # 5 条演示实例须全部建成：form_data 必须与流程当前 form_schema 的必填字段对齐，
    # 任一流程字段契约漂移都会在此暴露（曾有报销实例因缺「报销事由」被静默跳过）
    assert ApprovalInstance.objects.filter(pk__in=FLOW_INSTANCE_PKS).count() == len(FLOW_INSTANCE_PKS)
    assert (
        ApprovalRequest.objects.filter(pk__in=[f"6eed0002-0000-4000-8000-00000000000{i}" for i in range(1, 6)]).count()
        == 5
    )
    assert Leave.objects.filter(pk__in=LEAVE_PKS).count() == 4
    assert DynamicFormSubmission.objects.count() >= 2
    assert MessageContent.objects.filter(title__startswith="演示：").count() == 2
    assert UserInfo.objects.filter(username="demo_flow_lily").exists()

    # 演示账号必须可登录：演示在途单需要本人处理/撤回（不可登录账号会让演示单永久卡死，
    # 并因「在途实例存在时流程节点不可编辑」把演示流程一并锁死）
    lily = UserInfo.objects.get(username="demo_flow_lily")
    chen = UserInfo.objects.get(username="demo_flow_chen")
    assert lily.has_usable_password()
    assert chen.has_usable_password()

    # 演示委托的委托人必须是演示账号：超管作委托人会让超管在所有流程的待办被整体转走
    delegation = ApprovalDelegation.objects.get(pk="6eed0009-0000-4000-8000-000000000001")
    assert delegation.delegator_id == lily.pk
    assert delegation.delegate_id == chen.pk

    # 内置流程节点已被演示改写（uid 含 demo_flow_ 标记）
    node = ApprovalFlowNode.objects.filter(flow__code="demo_leave").first()
    assert "demo_flow_" in node.assignee_value

    call_command("seed_demo_clean")

    # 业务数据清零
    assert ApprovalInstance.objects.filter(pk__in=FLOW_INSTANCE_PKS).count() == 0
    assert Leave.objects.filter(pk__in=LEAVE_PKS).count() == 0
    assert MessageContent.objects.filter(title__startswith="演示：").count() == 0
    assert DeptInfo.objects.filter(code__in=["demo_rd", "demo_fin"]).count() == 0
    assert AiKnowledgeDocument.objects.filter(path__startswith="upload/演示-").count() == 0

    # 回滚：流程节点审批人恢复种子值、演示版本快照清空、版本号回落
    node.refresh_from_db()
    assert node.assignee_value == "xadmin,isummer"
    assert ApprovalFlowVersion.objects.filter(remark="演示审批人配置").count() == 0
    flow = ApprovalFlow.objects.get(code="demo_leave")
    assert flow.version == 1

    # 回滚：演示部门负责人恢复种子值（None）
    demo_dept = DeptInfo.objects.filter(code="demo").first()
    if demo_dept is not None:
        assert demo_dept.leader_id is None

    # 演示用户清零（硬删或降级软删都不可再查询到活跃行）
    assert UserInfo.all_objects.filter(username__startswith="demo_").count() == 0


# ---------------------------------------------------------------- 用户清理口径


def test_users_clean_only_keeps_command_scoped_accounts(db):
    UserInfo.objects.create_user("demo_0001", password=None, nickname="批量演示用户")
    UserInfo.objects.create_user("demo_flow_lily", password=None, nickname="演示申请人")

    call_command("seed_demo_users", clean_only=True)

    assert not UserInfo.all_objects.filter(username="demo_0001").exists()
    assert UserInfo.all_objects.filter(username="demo_flow_lily").exists()
