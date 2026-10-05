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
