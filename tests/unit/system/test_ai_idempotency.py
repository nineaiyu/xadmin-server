# -*- coding: utf-8 -*-
"""AI 动作执行幂等（AI-4）单测：draft_id 稳定性 + 首次结果复用 + force 通道。

幂等窗口内重复提交返回首次结果并标记 deduplicated；失败结果不缓存（允许立即重试）；
force=True 跳过幂等（用户确认后的「仍要执行」）。
"""

import pytest

from system.utils.ai_idempotency import (
    IDEMPOTENCY_TTL,
    draft_id,
    execute_idempotent,
    find_result,
    store_result,
)

pytestmark = pytest.mark.django_db


class TestDraftId:
    def test_stable_and_param_order_insensitive(self, superuser):
        first = draft_id(superuser, "leave.submit", {"a": 1, "b": 2})
        second = draft_id(superuser, "leave.submit", {"b": 2, "a": 1})
        assert first == second and len(first) == 32

    def test_differs_by_user_action_and_params(self, superuser, normal_user):
        base = draft_id(superuser, "leave.submit", {"a": 1})
        assert base != draft_id(normal_user, "leave.submit", {"a": 1})
        assert base != draft_id(superuser, "dform.submit", {"a": 1})
        assert base != draft_id(superuser, "leave.submit", {"a": 2})

    def test_handles_non_json_values(self, superuser):
        # 业务参数可能含 UUID / datetime（default=str 兜底，不抛异常）
        import datetime

        value = draft_id(superuser, "x", {"when": datetime.datetime(2030, 1, 1)})
        assert value and isinstance(value, str)


class TestExecuteIdempotent:
    def test_second_call_returns_first_result(self, superuser):
        calls = []

        def executor(user, action, params):
            calls.append(params)
            return {"ok": True, "detail": "done", "data": {"n": 1}}

        first = execute_idempotent(superuser, "leave.submit", {"a": 1}, executor)
        second = execute_idempotent(superuser, "leave.submit", {"a": 1}, executor)
        assert first["ok"] is True and "deduplicated" not in first
        assert second["deduplicated"] is True and second["data"] == {"n": 1}
        assert len(calls) == 1  # 第二次没有真正执行

    def test_force_bypasses_dedup(self, superuser):
        calls = []

        def executor(user, action, params):
            calls.append(params)
            return {"ok": True, "detail": "done", "data": {}}

        execute_idempotent(superuser, "leave.submit", {"a": 1}, executor)
        forced = execute_idempotent(superuser, "leave.submit", {"a": 1}, executor, force=True)
        assert "deduplicated" not in forced
        assert len(calls) == 2

    def test_failure_not_cached(self, superuser):
        calls = []

        def executor(user, action, params):
            calls.append(params)
            return {"ok": False, "detail": "boom", "data": {}}

        execute_idempotent(superuser, "leave.submit", {"a": 1}, executor)
        again = execute_idempotent(superuser, "leave.submit", {"a": 1}, executor)
        assert again["ok"] is False and len(calls) == 2  # 失败可立即重试

    def test_draft_id_in_result(self, superuser):
        result = execute_idempotent(superuser, "x", {"a": 1}, lambda u, a, p: {"ok": True, "data": {}})
        assert result["draft_id"] == draft_id(superuser, "x", {"a": 1})

    def test_store_and_find_raw(self, superuser):
        key = draft_id(superuser, "x", {})
        assert find_result(superuser, key) is None
        store_result(superuser, key, {"ok": True, "data": {"k": "v"}})
        assert find_result(superuser, key)["data"] == {"k": "v"}
        assert IDEMPOTENCY_TTL == 600
