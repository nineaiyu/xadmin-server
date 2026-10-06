# -*- coding: utf-8 -*-
"""审批终态回写注册表（approval/biz_sync.py）+ 信号分发链路单元测试。

约定：业务 app 在自身 config.py 声明 ``APPROVAL_BIZ_SYNCERS``（biz_type → 导入
路径），注册表应用声明优先、内置表兜底——新增审批回写业务零核心文件改动。
"""

import pytest

from approval import biz_sync
from approval.biz_sync import BUILTIN_BIZ_SYNCERS, get_biz_syncer, registered_biz_types
from approval.models.approval_instance import ApprovalInstance
from approval.signal import approval_instance_finished

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_registry_cache():
    """config.py 收集结果带进程级缓存，用例间必须清空。"""
    biz_sync._app_biz_syncers.cache_clear()
    yield
    biz_sync._app_biz_syncers.cache_clear()


class TestRegistryLookup:
    def test_builtin_leave_syncer_resolves(self):
        """内置表：leave 同步器可解析为可调用对象。"""
        syncer = get_biz_syncer("leave")
        assert callable(syncer)
        assert syncer.__name__ == "sync_leave_instance"

    def test_app_declared_syncer_resolves(self):
        """应用声明（demo/config.py 的 demo_book）经 config.py 收集解析。"""
        syncer = get_biz_syncer("demo_book")
        assert callable(syncer)
        assert syncer.__name__ == "sync_book_instance"

    def test_app_declared_syncer_resolves_dataset(self):
        """应用声明（dataset/config.py 的 dform_submission）同样可解析。"""
        assert get_biz_syncer("dform_submission").__name__ == "sync_dform_instance"

    def test_unknown_biz_type_returns_none(self):
        assert get_biz_syncer("no_such_biz") is None

    def test_registered_biz_types_covers_builtin_and_declared(self):
        registered = set(registered_biz_types())
        assert set(BUILTIN_BIZ_SYNCERS) <= registered
        assert {"demo_book", "dform_submission"} <= registered


class TestSignalDispatch:
    """approval_instance_finished 信号 → 注册表分发的端到端行为。"""

    def _make_instance(self, biz_type: str) -> ApprovalInstance:
        from approval.models.approval import ApprovalFlow

        flow = ApprovalFlow.objects.create(name="回写测试流", code="biz-sync-test")
        return ApprovalInstance.objects.create(
            flow=flow, flow_name=flow.name, title="回写测试", biz_type=biz_type, biz_id="1"
        )

    def test_dispatch_reaches_business_syncer(self):
        """信号发送 → 接收器按 biz_type 解析同步器执行（biz_type 不匹配时同步器自认领跳过）。"""
        instance = self._make_instance("leave")
        # leave 同步器按 biz_id 查 Leave 行：不存在则跳过（幂等），不抛错即链路通
        approval_instance_finished.send(sender=type(instance), instance=instance, status="APPROVED", reason="")

    def test_dispatch_unknown_biz_type_is_swallowed(self):
        """未注册 biz_type：接收器记警告跳过，不向发送方抛错（回写不阻断审批）。"""
        instance = self._make_instance("ghost_biz")
        approval_instance_finished.send(sender=type(instance), instance=instance, status="APPROVED", reason="")

    def test_syncer_exception_does_not_propagate(self, monkeypatch):
        """同步器异常被接收器兜底：审批状态机不被业务回写故障打断。"""

        def boom(*args, **kwargs):
            raise RuntimeError("sync failed")

        monkeypatch.setattr(biz_sync, "get_biz_syncer", lambda biz_type: boom if biz_type else None)
        instance = self._make_instance("leave")
        approval_instance_finished.send(sender=type(instance), instance=instance, status="REJECTED", reason="x")


class TestDemoBookSyncModifier:
    """demo 书籍终态回写的修改人语义：回写只推进状态，不覆盖 modifier。

    终态回写是系统驱动的状态流转；若把 modifier 覆盖为审批实例创建人，
    审批期间管理员编辑书籍留下的真实修改人痕迹会被抹掉。
    """

    def _make_users(self):
        from identity.models import UserInfo

        applicant = UserInfo.objects.create_user(username="book_applicant", password="Test@123456")
        editor = UserInfo.objects.create_user(username="book_editor", password="Test@123456")
        return applicant, editor

    def _make_book(self, creator, modifier):
        from demo.models import Book

        return Book.objects.create(
            name="回写书籍",
            isbn="978-7-000-00000-1",
            author="作者",
            admin=creator,
            admin2=creator,
            creator=creator,
            modifier=modifier,
        )

    def _make_instance(self, creator, book):
        from approval.models.approval import ApprovalFlow
        from demo.services import BOOK_BIZ_TYPE

        flow = ApprovalFlow.objects.create(name="回写修改人流", code="biz-sync-modifier")
        return ApprovalInstance.objects.create(
            flow=flow,
            flow_name=flow.name,
            title="回写修改人",
            biz_type=BOOK_BIZ_TYPE,
            biz_id=str(book.pk),
            creator=creator,
        )

    def test_terminal_writeback_keeps_modifier(self):
        """终态回写后 modifier 保持回写前的值（不覆盖为审批实例创建人）。"""
        from demo.models import Book
        from demo.services import sync_book_instance

        applicant, editor = self._make_users()
        book = self._make_book(creator=applicant, modifier=editor)
        instance = self._make_instance(creator=applicant, book=book)

        sync_book_instance(instance, "APPROVED")

        book.refresh_from_db()
        assert book.status == Book.Status.ON_SHELF
        assert book.modifier == editor

    def test_third_party_edit_during_approval_not_overwritten(self):
        """审批期间第三方编辑书籍后，终态回写不抹掉其留下的修改人痕迹。"""
        from demo.models import Book
        from demo.services import sync_book_instance

        applicant, editor = self._make_users()
        book = self._make_book(creator=applicant, modifier=applicant)
        instance = self._make_instance(creator=applicant, book=book)

        # 审批期间管理员编辑书籍（真实修改人 = 编辑者）
        book.name = "回写书籍（已修订）"
        book.modifier = editor
        book.save(update_fields=["name", "modifier", "updated_time"])

        sync_book_instance(instance, "APPROVED")

        book.refresh_from_db()
        assert book.status == Book.Status.ON_SHELF
        assert book.modifier == editor
