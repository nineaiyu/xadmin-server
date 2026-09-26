# -*- coding: utf-8 -*-
"""图书上架审批示例：提交流转 + 终态回写 + 删除二次确认（敏感操作审批）。

覆盖 demo app 对两条框架链路的接入（与文档「审批流引擎 / 敏感操作审批」口径一致）：

1. 审批流引擎：``submit`` → PENDING（挂流程实例），终态经 ``approval_instance_finished``
   信号回写 ``Book.status``（通过 → 已上架并启用、驳回 → 已驳回）；
2. 敏感操作审批：删除挂载 ``ApprovalRequired``，配置命中后 412 + 一次性令牌重放
   （前端 http 层已内置令牌暂存与自动携带，这里验证服务端协议闭环）。
"""

import pytest

from approval.models.approval import (
    ApprovalFlow,
    ApprovalFlowNode,
    ApprovalNodeTask,
    ApprovalRequest,
)
from approval.utils.approval import approve_request
from approval.utils.approval_flow import approve_task, reject_task
from common.core.config import SysConfig
from demo.models import Book
from demo.services import BOOK_BIZ_TYPE, BOOK_FLOW_CODE
from system.models import UserInfo

pytestmark = pytest.mark.django_db

BOOK_LIST_URL = "/api/demo/book"


@pytest.fixture
def approver(db):
    """流程审批人：与申请人必须不同（引擎恒剔除申请人本人）。"""
    return UserInfo.objects.create_superuser(
        username="book_approver", email="book_approver@example.com", password="Test@123456"
    )


def make_book_flow(assignee_value: str):
    flow = ApprovalFlow.objects.create(name="书籍上架审批", code=BOOK_FLOW_CODE)
    ApprovalFlowNode.objects.create(
        flow=flow,
        name="图书管理员审批",
        order=1,
        approve_type=ApprovalFlowNode.ApproveType.OR,
        assignee_type=ApprovalFlowNode.AssigneeType.USER,
        assignee_value=assignee_value,
    )
    return flow


@pytest.fixture
def book(superuser):
    from system.models import UploadFile

    upload = UploadFile.objects.create(
        filename="book.pdf", filesize=100, mime_type="application/pdf", md5sum="b" * 32, creator=superuser
    )
    return Book.objects.create(
        name="待上架书籍",
        isbn="978-7-000-00000-0",
        author="作者",
        admin=superuser,
        admin2=superuser,
        file=upload,
    )


class TestBookOnShelfFlow:
    def test_submit_creates_instance_and_pending(self, auth_client, approver, book):
        make_book_flow(approver.username)
        resp = auth_client.post(f"{BOOK_LIST_URL}/{book.pk}/submit")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        book.refresh_from_db()
        assert book.status == Book.Status.PENDING
        assert book.instance_id is not None
        assert book.instance.biz_type == BOOK_BIZ_TYPE
        assert book.instance.biz_id == str(book.pk)
        assert ApprovalNodeTask.objects.filter(
            instance=book.instance, assignee=approver, status=ApprovalNodeTask.Status.PENDING
        ).exists()

    def test_submit_without_flow_is_rejected(self, auth_client, book):
        """无可用流程定义时 fail-closed（不静默直通）。"""
        resp = auth_client.post(f"{BOOK_LIST_URL}/{book.pk}/submit")
        assert resp.status_code == 200
        assert resp.data["code"] == 1001
        book.refresh_from_db()
        assert book.status == Book.Status.DRAFT

    def test_approve_writes_back_on_shelf(self, auth_client, approver, book):
        make_book_flow(approver.username)
        auth_client.post(f"{BOOK_LIST_URL}/{book.pk}/submit")
        book.refresh_from_db()
        task = ApprovalNodeTask.objects.get(instance_id=book.instance_id, assignee=approver)
        ok, detail = approve_task(task.pk, approver, "同意（测试）")
        assert ok, detail
        book.refresh_from_db()
        assert book.status == Book.Status.ON_SHELF
        assert book.is_active is True
        # 审批通过落上架时间（业务痕迹）
        assert book.on_shelf_time is not None

    def test_reject_writes_back_rejected(self, auth_client, approver, book):
        make_book_flow(approver.username)
        auth_client.post(f"{BOOK_LIST_URL}/{book.pk}/submit")
        book.refresh_from_db()
        task = ApprovalNodeTask.objects.get(instance_id=book.instance_id, assignee=approver)
        ok, detail = reject_task(task.pk, approver, "信息不全（测试）")
        assert ok, detail
        book.refresh_from_db()
        assert book.status == Book.Status.REJECTED

    def test_submit_twice_conflicts(self, auth_client, approver, book):
        """审批中不可重复提交。"""
        make_book_flow(approver.username)
        auth_client.post(f"{BOOK_LIST_URL}/{book.pk}/submit")
        resp = auth_client.post(f"{BOOK_LIST_URL}/{book.pk}/submit")
        assert resp.data["code"] == 1001


class TestBookRecycleBin:
    """回收站（软删除）：删除进回收站 → 恢复 / 物理清除。"""

    def test_soft_delete_restore_and_purge(self, auth_client, book):
        # 删除 = 软删（主列表不可见，回收站可见）
        resp = auth_client.delete(f"{BOOK_LIST_URL}/{book.pk}")
        assert resp.data["code"] == 1000
        assert not Book.objects.filter(pk=book.pk).exists()
        assert Book.all_objects.filter(pk=book.pk, deleted_at__isnull=False).exists()

        # 回收站列表（含删除时间，供抽屉展示）
        resp = auth_client.get(f"{BOOK_LIST_URL}/recycle")
        assert resp.status_code == 200, resp.data
        assert resp.data["data"]["total"] == 1
        row = resp.data["data"]["results"][0]
        assert row["pk"] == book.pk
        assert row["deleted_at"] is not None

        # 恢复
        resp = auth_client.patch(f"{BOOK_LIST_URL}/recycle/restore", {"pks": [book.pk]}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        book.refresh_from_db()
        assert book.deleted_at is None
        assert Book.objects.filter(pk=book.pk).exists()

        # 再删后物理清除（硬删，all_objects 也不可查）
        auth_client.delete(f"{BOOK_LIST_URL}/{book.pk}")
        resp = auth_client.delete(f"{BOOK_LIST_URL}/recycle/purge", {"pks": [book.pk]}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        assert not Book.all_objects.filter(pk=book.pk).exists()


class TestBlockSwitch:
    def test_block_switch_writes_is_active(self, auth_client, book):
        """`block`（input_type=boolean 教学字段）：列表开关走 partialUpdate 切换启用状态。"""
        assert book.is_active is False
        resp = auth_client.patch(f"{BOOK_LIST_URL}/{book.pk}", {"block": True}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        book.refresh_from_db()
        assert book.is_active is True

        resp = auth_client.patch(f"{BOOK_LIST_URL}/{book.pk}", {"block": False}, format="json")
        assert resp.data["code"] == 1000
        book.refresh_from_db()
        assert book.is_active is False


class TestBookDeleteApproval:
    def _enable(self):
        SysConfig.set_value("APPROVAL_REQUIRED_PATHS", [r"api/demo/book/"])

    def test_default_config_does_not_intercept(self, auth_client, book):
        """默认清单为空 = 休眠：删除直接执行（与既有部署行为一致）。"""
        resp = auth_client.delete(f"{BOOK_LIST_URL}/{book.pk}")
        assert resp.data["code"] == 1000
        assert not Book.objects.filter(pk=book.pk).exists()

    def test_destroy_requires_approval_and_token_replay(self, auth_client, approver, book):
        self._enable()
        resp = auth_client.delete(f"{BOOK_LIST_URL}/{book.pk}")
        assert resp.status_code == 412
        assert resp.data["code"] == 1002
        assert resp.data["type"] == "approval_required"
        # 业务未执行，留下待审批单（申请人 = 发起删除的用户）
        assert Book.objects.filter(pk=book.pk).exists()
        approval = ApprovalRequest.objects.get(status=ApprovalRequest.Status.PENDING)
        assert approval.method == "DELETE"

        ok, detail = approve_request(approval, approver)
        assert ok, detail
        # 携一次性令牌重放（前端协议：审批通过后重发同一请求自动带 X-Approval-Id）
        resp = auth_client.delete(f"{BOOK_LIST_URL}/{book.pk}", HTTP_X_APPROVAL_ID=str(approval.pk))
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        assert not Book.objects.filter(pk=book.pk).exists()
