# -*- coding: utf-8 -*-
"""敏感操作审批：拦截建单 / 指纹消费 / 审批动作 / 超时清理 / 取值域。

标准配对：申请人 = normal_user，审批人 = superuser（默认审批人集合 = 在用超管
排除申请人；单超管申请人建单必然「审批人空集」报错，见空集专项用例）。
"""

import datetime

import pytest
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.viewsets import ViewSet

from common.core.approval import ApprovalRequired
from common.core.response import ApiResponse
from system.models.approval import ApprovalRequest
from system.tasks import auto_clean_approval_job, auto_expire_approval_job
from system.utils.approval import (
    approve_request,
    can_approve,
    cancel_request,
    clean_expired_approvals,
    create_approval,
    expire_pending_approvals,
    reject_request,
)
from system.views.admin.approval import ApprovalRequestViewSet

pytestmark = pytest.mark.django_db

PENDING_STATUS = ApprovalRequest.Status.PENDING
APPROVED_STATUS = ApprovalRequest.Status.APPROVED


class DummyViewSet(ViewSet):
    """测试对象

    permission_classes = AllowAny：本套件目标是审批拦截链路而非菜单权限矩阵
    （自定义 IsAuthenticated 需要 RBAC 菜单授权，走审批中心 ViewSet 用例验证）。
    """

    permission_classes = [AllowAny]

    @ApprovalRequired()
    def destroy(self, request, pk=None):
        return ApiResponse(data={"executed": True, "pk": pk})

    @ApprovalRequired()
    def batch(self, request):
        return ApiResponse(data={"executed": True, "pks": list(request.data)})


def _enable_interception(paths=None):
    """开启审批拦截（默认空清单 = 休眠）。"""
    from common.core.config import SysConfig

    SysConfig.set_value("APPROVAL_REQUIRED_PATHS", paths or [r"^/api/test/"])


def _request(user, method, path, data=None, headers=None):
    factory = APIRequestFactory()
    request = getattr(factory, method)(path, data, format="json", **(headers or {}))
    force_authenticate(request, user=user)
    return request


def _dispatch(user, method, path, data=None, headers=None):
    request = _request(user, method, path, data, headers)
    if method == "delete":
        response = DummyViewSet.as_view({"delete": "destroy"})(request, pk="1")
    else:
        response = DummyViewSet.as_view({"post": "batch"})(request)
    return response


def _submit(applicant, path="/api/test/1", data=None):
    return _dispatch(applicant, "delete", path, data)


def _pending(applicant):
    return ApprovalRequest.objects.filter(creator=applicant, status=PENDING_STATUS).first()


class TestInterception:
    """拦截判定与建单。"""

    def test_dormant_when_paths_empty(self, superuser):
        """默认空清单 = 休眠：直接放行业务，不建审批单。"""
        response = _dispatch(superuser, "delete", "/api/test/1")
        assert response.status_code == 200
        assert response.data["data"]["executed"] is True
        assert ApprovalRequest.objects.exists() is False

    def test_invalid_regex_skipped(self, superuser):
        """非法正则跳过匹配（不 500、不放任整个清单失效），业务放行。"""
        _enable_interception([r"^/api/test/("])
        response = _dispatch(superuser, "delete", "/api/test/1")
        assert response.status_code == 200
        assert ApprovalRequest.objects.exists() is False

    def test_creates_pending_and_blocks_business(self, superuser, normal_user):
        """命中清单：建 PENDING 单返回 412/1002，业务未执行，body 快照脱敏。"""
        _enable_interception()
        response = _submit(normal_user, data={"password": "Secret@123", "note": "x"})
        assert response.status_code == 412
        assert response.data["code"] == 1002
        assert response.data["type"] == "approval_required"
        approval_id = response.data["data"]["approval_id"]
        record = ApprovalRequest.objects.get(pk=approval_id)
        assert record.status == PENDING_STATUS
        assert record.method == "DELETE"
        assert record.path == "/api/test/1"
        assert record.object_pk == "1"
        assert record.creator == normal_user
        assert record.module == "测试对象"
        # 快照脱敏：密码掩码入库
        assert record.params["password"] == "*" * len("Secret@123")
        assert record.params["note"] == "x"

    def test_duplicate_submit_reuses_pending(self, superuser, normal_user):
        """同指纹重复提交复用在途单，不重复建单。"""
        _enable_interception()
        first = _submit(normal_user)
        second = _submit(normal_user)
        assert first.data["data"]["approval_id"] == second.data["data"]["approval_id"]
        assert ApprovalRequest.objects.count() == 1

    def test_different_body_creates_new_approval(self, superuser, normal_user):
        """batch 不同 body 指纹不同：不串用同一审批单。"""
        _enable_interception([r"^/api/test/batch$"])
        response_a = _dispatch(normal_user, "post", "/api/test/batch", ["pk-a", "pk-b"])
        response_b = _dispatch(normal_user, "post", "/api/test/batch", ["pk-c"])
        assert response_a.data["data"]["approval_id"] != response_b.data["data"]["approval_id"]
        assert ApprovalRequest.objects.count() == 2

    def test_empty_approvers_rejects_creation(self, normal_user):
        """审批人集合为空（配置的角色无成员）→ 建单直接报错，不产生永久 PENDING。

        直接调建单工具（绕开 DRF 异常处理器的 set_rollback，保持测试事务可用）。
        """
        from rest_framework.request import Request

        from common.core.config import SysConfig

        _enable_interception()
        SysConfig.set_value("APPROVAL_APPROVER_ROLES", ["nonexistent-role"])
        factory = APIRequestFactory()
        raw = factory.delete("/api/test/1", format="json")
        force_authenticate(raw, user=normal_user)
        # 直接调用工具函数时手动包一层 DRF Request（dispatch 外没有自动包装）
        request = Request(raw, authenticators=())
        view = DummyViewSet()
        view.kwargs = {"pk": "1"}
        with pytest.raises(Exception) as exc_info:
            create_approval(view, request)
        # 文案经 gettext 活动语言渲染（zh/en），断言取两语关键字并集
        assert "approver" in str(exc_info.value).lower() or "审批人" in str(exc_info.value)
        assert ApprovalRequest.objects.exists() is False


class TestConsume:
    """令牌消费：一次性、有效期、指纹一致性。"""

    def test_approve_then_consume_once(self, superuser, normal_user):
        """审批人通过 → 携令牌重发放行 → 二次使用 403。"""
        _enable_interception()
        response = _submit(normal_user)
        approval = ApprovalRequest.objects.get(pk=response.data["data"]["approval_id"])

        ok, _detail = approve_request(approval, superuser)
        assert ok is True

        # 携令牌重发：放行
        response_consume = _dispatch(
            normal_user, "delete", "/api/test/1", headers={"HTTP_X_APPROVAL_ID": str(approval.pk)}
        )
        assert response_consume.status_code == 200
        assert response_consume.data["data"]["executed"] is True
        approval.refresh_from_db()
        assert approval.consume_time is not None

        # 二次携令牌：403
        response_reuse = _dispatch(
            normal_user, "delete", "/api/test/1", headers={"HTTP_X_APPROVAL_ID": str(approval.pk)}
        )
        assert response_reuse.status_code == 403

    def test_consume_lost_race_rejected(self, superuser, normal_user, monkeypatch):
        """并发重发竞态：条件更新 rowcount=0（另一请求已先消费）时按「已使用」拒绝。

        一次性令牌必须原子消费——原实现「查 consume_time → 再 save」存在 TOCTOU，
        并发重发可双消费。
        """
        from django.db.models import QuerySet

        _enable_interception()
        response = _submit(normal_user)
        approval = ApprovalRequest.objects.get(pk=response.data["data"]["approval_id"])
        ok, _detail = approve_request(approval, superuser)
        assert ok is True

        # 模拟另一并发请求已抢先消费：条件更新影响 0 行
        monkeypatch.setattr(QuerySet, "update", lambda self, **kwargs: 0)
        retry = _dispatch(normal_user, "delete", "/api/test/1", headers={"HTTP_X_APPROVAL_ID": str(approval.pk)})
        assert retry.status_code == 403
        monkeypatch.undo()
        approval.refresh_from_db()
        assert approval.consume_time is None  # 本次未消费成功，令牌状态未被污染

    def test_resend_while_pending_returns_1002(self, superuser, normal_user):
        """审批在途时重发：返回同单 1002 而非 403。"""
        _enable_interception()
        response = _submit(normal_user)
        approval_id = response.data["data"]["approval_id"]
        response_retry = _dispatch(normal_user, "delete", "/api/test/1", headers={"HTTP_X_APPROVAL_ID": approval_id})
        assert response_retry.status_code == 412
        assert response_retry.data["data"]["approval_id"] == approval_id

    def test_fingerprint_mismatch_marks_failed(self, superuser, normal_user):
        """「批 A、B」的令牌拿去执行「C、D」：403 且审批单置 FAILED（留审计）。"""
        _enable_interception([r"^/api/test/batch$"])
        response = _dispatch(normal_user, "post", "/api/test/batch", ["pk-a", "pk-b"])
        approval = ApprovalRequest.objects.get(pk=response.data["data"]["approval_id"])
        ok, _detail = approve_request(approval, superuser)
        assert ok

        response_abuse = _dispatch(
            normal_user,
            "post",
            "/api/test/batch",
            ["pk-c", "pk-d"],
            headers={"HTTP_X_APPROVAL_ID": str(approval.pk)},
        )
        assert response_abuse.status_code == 403
        approval.refresh_from_db()
        assert approval.status == ApprovalRequest.Status.FAILED

    def test_token_belongs_to_applicant(self, superuser, normal_user):
        """令牌绑定申请人：他人携令牌重发 403。"""
        _enable_interception()
        response = _submit(normal_user)
        approval = ApprovalRequest.objects.get(pk=response.data["data"]["approval_id"])
        approve_request(approval, superuser)

        response_other = _dispatch(superuser, "delete", "/api/test/1", headers={"HTTP_X_APPROVAL_ID": str(approval.pk)})
        assert response_other.status_code == 403

    def test_rejected_expired_cancelled_rejected(self, superuser, normal_user):
        """驳回 / 过期 / 撤回的令牌均拒绝。"""
        _enable_interception()

        # 驳回：403 带原因
        response = _submit(normal_user)
        approval = ApprovalRequest.objects.get(pk=response.data["data"]["approval_id"])
        reject_request(approval, superuser, "资料不全")
        response_rejected = _dispatch(
            normal_user, "delete", "/api/test/1", headers={"HTTP_X_APPROVAL_ID": str(approval.pk)}
        )
        assert response_rejected.status_code == 403
        assert "资料不全" in response_rejected.data["detail"]

        # 过期：403 且审批单置 EXPIRED
        response = _submit(normal_user)
        approval = ApprovalRequest.objects.get(pk=response.data["data"]["approval_id"])
        approve_request(approval, superuser)
        approval.expired_at = timezone.now() - datetime.timedelta(seconds=1)
        approval.save(update_fields=["expired_at", "updated_time"])
        response_expired = _dispatch(
            normal_user, "delete", "/api/test/1", headers={"HTTP_X_APPROVAL_ID": str(approval.pk)}
        )
        assert response_expired.status_code == 403
        approval.refresh_from_db()
        assert approval.status == ApprovalRequest.Status.EXPIRED

        # 撤回：403
        response = _submit(normal_user)
        approval = ApprovalRequest.objects.get(pk=response.data["data"]["approval_id"])
        cancel_request(approval, normal_user)
        response_cancelled = _dispatch(
            normal_user, "delete", "/api/test/1", headers={"HTTP_X_APPROVAL_ID": str(approval.pk)}
        )
        assert response_cancelled.status_code == 403

    def test_invalid_token_forbidden(self, superuser, normal_user):
        """伪造的令牌 403。"""
        _enable_interception()
        response = _dispatch(normal_user, "delete", "/api/test/1", headers={"HTTP_X_APPROVAL_ID": "0" * 32})
        assert response.status_code == 403

    def test_non_uuid_token_forbidden_not_500(self, superuser, normal_user):
        """非 UUID 令牌 403（回归：直接进 filter 会抛 Django ValidationError → 500）。"""
        _enable_interception()
        for bad_token in ("not-a-uuid", "123"):
            response = _dispatch(normal_user, "delete", "/api/test/1", headers={"HTTP_X_APPROVAL_ID": bad_token})
            assert response.status_code == 403, bad_token


class TestApprovalActions:
    """审批动作权限与状态校验。"""

    def test_applicant_cannot_self_approve(self, superuser, normal_user):
        """申请人不能审批自己的单。"""
        _enable_interception()
        response = _submit(normal_user)
        approval = ApprovalRequest.objects.get(pk=response.data["data"]["approval_id"])
        ok, detail = approve_request(approval, normal_user)
        assert ok is False
        approval.refresh_from_db()
        assert approval.status == PENDING_STATUS

    def test_batch_approve_requires_approver(self, superuser, normal_user):
        """批量通过同样要求审批人身份（与单条 approve/reject 口径一致）。"""
        _enable_interception()
        response = _submit(normal_user)
        approval_id = response.data["data"]["approval_id"]

        # 直接调用 action：DRF Request 才有 .user（force_authenticate 走 _force_auth_user）
        from rest_framework.request import Request as DRFRequest

        request = DRFRequest(
            _request(normal_user, "post", "/api/system/approvals/batch-approve", data={"pks": [approval_id]})
        )
        view = ApprovalRequestViewSet()
        view.request = request
        view.format_kwarg = None
        with pytest.raises(PermissionDenied):
            view.batch_approve(request)
        assert ApprovalRequest.objects.get(pk=approval_id).status == PENDING_STATUS

    def test_approver_role_scope(self, superuser, normal_user):
        """APPROVAL_APPROVER_ROLES 限定审批人：角色成员可审批，他人不可。"""
        from common.core.config import SysConfig

        _enable_interception()
        SysConfig.set_value("APPROVAL_APPROVER_ROLES", ["common"])
        # normal_user 属 common 角色；superuser 不属配置角色
        assert can_approve(normal_user) is True
        assert can_approve(superuser) is False

        # 申请人 superuser（不在角色内 → 非审批人），审批人 normal_user（角色成员）
        response = _submit(superuser)
        approval = ApprovalRequest.objects.get(pk=response.data["data"]["approval_id"])
        ok, _detail = approve_request(approval, normal_user)
        assert ok is True
        approval.refresh_from_db()
        assert approval.approver == normal_user

    def test_viewset_approve_and_scope_list(self, superuser, normal_user, role, menu_factory, api_client):
        """审批中心 ViewSet：通过动作 + 待我审批/我发起的取值域。"""
        # normal_user 走自定义 IsAuthenticated 的 RBAC 菜单链路，需授列表权限
        perm = menu_factory("审批查询", path="api/system/approvals$", method="GET")
        role.menu.add(perm)

        _enable_interception()
        response = _submit(normal_user)
        approval_id = response.data["data"]["approval_id"]

        request = _request(superuser, "post", f"/api/system/approvals/{approval_id}/approve")
        ApprovalRequestViewSet.as_view({"post": "approve"})(request, pk=approval_id)
        assert ApprovalRequest.objects.get(pk=approval_id).status == APPROVED_STATUS

        # 待我审批页签（超管）：已通过单不在其中（total 口径）
        request = _request(superuser, "get", "/api/system/approvals", {"scope": "pending"})
        response_pending = ApprovalRequestViewSet.as_view({"get": "list"})(request)
        assert response_pending.data["data"]["total"] == 0

        # 我发起的页签（normal_user）：可见自己发起的单。
        # 普通 user 的字段权限为空 → 序列化行被裁剪为空 dict，断言用 total 口径
        api_client.force_authenticate(user=normal_user)
        response_mine = api_client.get("/api/system/approvals", {"scope": "mine"})
        assert response_mine.data["code"] == 1000
        assert response_mine.data["data"]["total"] == 1

    def test_viewset_reject_requires_reason(self, superuser, normal_user, api_client):
        """驳回必填原因；驳回后审批单置 REJECTED。"""
        _enable_interception()
        response = _submit(normal_user)
        approval_id = response.data["data"]["approval_id"]

        api_client.force_authenticate(user=superuser)
        response_no_reason = api_client.post(f"/api/system/approvals/{approval_id}/reject", {}, format="json")
        assert response_no_reason.status_code == 400

        response_reject = api_client.post(
            f"/api/system/approvals/{approval_id}/reject", {"reason": "风险操作"}, format="json"
        )
        assert response_reject.data["code"] == 1000
        assert ApprovalRequest.objects.get(pk=approval_id).status == ApprovalRequest.Status.REJECTED


class TestLifecycleJobs:
    """超时过期与保留期清理。"""

    def test_expire_pending_approvals(self, superuser, normal_user):
        _enable_interception()
        _submit(normal_user)
        # 未超时：不动
        assert expire_pending_approvals(pending_days=3) == 0
        # 超时：PENDING → EXPIRED
        record = ApprovalRequest.objects.first()
        record.created_time = timezone.now() - datetime.timedelta(days=4)
        record.save(update_fields=["created_time"])
        assert expire_pending_approvals(pending_days=3) == 1
        record.refresh_from_db()
        assert record.status == ApprovalRequest.Status.EXPIRED
        # 0 = 不启用
        assert expire_pending_approvals(pending_days=0) == 0

    def test_clean_expired_approvals(self, superuser, normal_user):
        _enable_interception()
        _submit(normal_user)
        record = ApprovalRequest.objects.first()
        record.created_time = timezone.now() - datetime.timedelta(days=200)
        record.save(update_fields=["created_time"])
        assert clean_expired_approvals(keep_days=180) == 1
        assert ApprovalRequest.objects.exists() is False
        assert clean_expired_approvals(keep_days=0) == 0

    def test_periodic_tasks_wired(self, superuser, normal_user):
        """两个周期任务可执行（crontab 由 register_as_period_task 登记）。"""
        _enable_interception()
        _submit(normal_user)
        assert auto_expire_approval_job.apply().get() == 0
        assert auto_clean_approval_job.apply().get() == 0
