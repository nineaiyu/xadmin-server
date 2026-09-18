# -*- coding: utf-8 -*-
"""导入导出执行链：装配契约测试（ADR-036 的「先补契约测试」落地）。

任务内执行不再重放 WSGIRequest，改为 `common/core/task_request.py` 的显式上下文。
本文件以 **spy 视图集**（子类化真实视图集，行为不变、只记录）断言五个契约在真实执行链内
均可观测：

1. 提交者身份：`view.request.user` 与 thread-local 请求均指向提交者（权限/字段权限/creator）；
2. `view.action`：serializer 行为分支（如创建时密码规则）依赖它；
3. `view.format_kwarg`：`get_serializer_context` 依赖（漏设直接 AttributeError）；
4. `view.kwargs`：detail 路由参数（本链路为空 dict）；
5. thread-local 请求（creator 信号赋值 + 审计 request_uuid），出口必须清理。

另含：请求构造/上下文绑定的单元形态断言、分片任务的身份携带（meta.user_pk）与通知对象。
"""

import json
from types import SimpleNamespace

import pytest
from django.http import QueryDict

from common.core.task_request import bind_view_task_context, build_task_request
from server.utils import get_current_request
from system.models.dict import DataDict
from system.models.export import ExportRecord
from system.views.admin.dict import DataDictViewSet

pytestmark = pytest.mark.django_db

VIEW_PATH = f"{__name__}.ContractDictViewSet"
CSV_BODY = b"code,label\nctx-color,\xe5\xa5\x91\xe7\xba\xa6\xe8\x89\xb2\n"


class ContractDictViewSet(DataDictViewSet):
    """契约探针：记录任务执行期间视图可见的上下文（行为与父类一致）。"""

    captured: dict = {}

    def get_serializer(self, *args, **kwargs):
        type(self).captured = self._capture_context()
        return super().get_serializer(*args, **kwargs)

    def list(self, request, *args, **kwargs):
        """导出链路（export_data → list）也在此捕获。"""
        type(self).captured = self._capture_context()
        return super().list(request, *args, **kwargs)

    def _capture_context(self) -> dict:
        request = getattr(self, "request", None)
        current = get_current_request()
        return {
            "action": self.action,
            "format_kwarg": self.format_kwarg,
            "kwargs": dict(self.kwargs),
            "request_user_pk": getattr(getattr(request, "user", None), "pk", None),
            "query_string": getattr(request, "META", {}).get("QUERY_STRING", ""),
            "current_user_pk": getattr(getattr(current, "user", None), "pk", None),
        }


def _post_import_as(user, csv_body: bytes = CSV_BODY, action: str = "create"):
    """与同步 import-data 同协议提交 import-async（eager 下任务同步跑完）。"""
    from rest_framework.test import APIRequestFactory, force_authenticate

    factory = APIRequestFactory()
    request = factory.post(
        f"/api/system/dict/import-async?action={action}",
        csv_body,
        content_type="text/csv",
    )
    force_authenticate(request, user=user)
    # 经 spy 视图集提交：记录里的 view_path 指向 spy（任务侧按它 import_string）
    return ContractDictViewSet.as_view({"post": "import_async"})(request)


class TestBuildTaskRequest:
    def test_explicit_fields_and_identity(self):
        operator = SimpleNamespace(pk=7)
        request = build_task_request(
            method="get",
            path="/api/system/dict/export-data",
            query_params={"type": "csv", "tag": ["a", "b"]},
            body=b"x",
            user=operator,
            request_uuid="ctx-uuid",
        )
        assert request.method == "GET"
        assert request.path == "/api/system/dict/export-data"
        assert request.META["QUERY_STRING"] == "type=csv&tag=a&tag=b"
        assert request.GET.getlist("tag") == ["a", "b"]
        assert request.content_type == "application/json"
        assert request.body == b"x"
        # 两个读法都要满足：任务汇总直接读 request.user；DRF 侧认 _force_auth_user
        assert request.user is operator
        assert request._force_auth_user is operator
        assert request.request_uuid == "ctx-uuid"

    def test_raw_query_string_passthrough(self):
        """分片任务从 META 取原始查询串（不重复编码）。"""
        request = build_task_request(method="POST", path="/", query_params="action=create&task=true")
        assert request.GET["action"] == "create"
        assert request.GET["task"] == "true"


class TestBindViewTaskContext:
    def test_binds_all_view_contracts(self):
        view = ContractDictViewSet()
        operator = SimpleNamespace(pk=7)
        request = build_task_request(method="POST", path="/api/system/dict/import-data", user=operator)
        drf_request = bind_view_task_context(view, request, action="import_data", kwargs={"pk": "1"})
        assert view.request is drf_request
        assert view.action == "import_data"
        assert view.kwargs == {"pk": "1"}
        assert view.format_kwarg is None
        # 显式直通：不触发认证链即可读到提交者
        assert drf_request.user is operator

    def test_defaults_kwargs_to_empty_dict(self):
        view = ContractDictViewSet()
        bind_view_task_context(view, build_task_request(method="POST", path="/"), action="import_data")
        assert view.kwargs == {}
        assert view.format_kwarg is None


class TestAsyncImportContracts:
    def test_import_task_binds_request_context_and_creator(self, superuser):
        ContractDictViewSet.captured = {}
        response = _post_import_as(superuser)
        assert response.data["code"] == 1000

        captured = ContractDictViewSet.captured
        assert captured["action"] == "import_data"
        assert captured["format_kwarg"] is None
        assert captured["kwargs"] == {}
        assert captured["request_user_pk"] == superuser.pk
        assert captured["current_user_pk"] == superuser.pk
        # 异步导入动作来自记录（record.action），任务请求不重放原查询串——显式空查询串
        assert captured["query_string"] == ""
        # creator 信号经 thread-local 请求赋值
        row = DataDict.objects.get(code="ctx-color")
        assert row.creator_id == superuser.pk
        # 出口清理：任务结束后 thread-local 不残留（防跨任务串号）
        assert get_current_request() is None


class TestAsyncExportContracts:
    def test_export_task_binds_request_context_and_query(self, superuser):
        from system.tasks._export import run_async_export

        ContractDictViewSet.captured = {}
        DataDict.objects.create(code="ctx-hit", label="命中", creator=superuser)
        DataDict.objects.create(code="ctx-miss", label="未命中", creator=superuser)
        record = ExportRecord.objects.create(
            name="契约导出",
            module="数据字典",
            path="/api/system/dict/export-data",
            file_format="csv",
            params={},
            creator=superuser,
        )
        rows = run_async_export(str(record.pk), VIEW_PATH, {"type": "csv", "code": "ctx-hit"}, superuser.pk)

        captured = ContractDictViewSet.captured
        assert captured["action"] == "export_data"
        assert captured["format_kwarg"] == "csv"
        assert captured["request_user_pk"] == superuser.pk
        assert QueryDict(captured["query_string"])["code"] == "ctx-hit"
        # 查询参数确实进入 filterset：两行数据只导出一行
        assert rows == 1
        record.refresh_from_db()
        assert record.status == ExportRecord.Status.SUCCESS
        assert record.rows == 1
        assert record.file_id is not None


class TestBatchTaskIdentity:
    def test_batch_task_carries_submitter_identity(self, monkeypatch, superuser):
        from common import tasks as common_tasks
        from common.cache.redis import CacheList

        published = []

        class FakeMessage:
            def __init__(self, user, task_info):
                published.append((user, task_info))

            def publish(self):
                published.append("publish")

        monkeypatch.setattr(common_tasks, "ImportDataMessage", FakeMessage)
        ContractDictViewSet.captured = {}
        meta = {
            "task_id": "ctxjob_0",
            "task_index": 0,
            "task_count": 1,
            "action": "import_data",
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/api/system/dict/import-data",
            "QUERY_STRING": "action=create&task=true",
            "user_pk": superuser.pk,
        }
        try:
            info = common_tasks.background_task_view_set_job(
                view=VIEW_PATH,
                meta=meta,
                # 分片任务按原 action 重放（import_data 收 JSON 行数组 + task=false 走同步）
                data=json.dumps([{"code": "batch-ctx", "label": "分片"}]),
                action_map={"post": "import_data"},
            )
        finally:
            CacheList("view_task_ctxjob", timeout=3600 * 24).delete()

        assert info["state"] is True
        captured = ContractDictViewSet.captured
        assert captured["request_user_pk"] == superuser.pk
        assert captured["current_user_pk"] == superuser.pk
        assert QueryDict(captured["query_string"])["action"] == "create"
        # 通知对象 = 显式身份（meta.user_pk），不再依赖 WSGIRequest 上的 user
        assert published and published[0][0].pk == superuser.pk
        assert DataDict.objects.get(code="batch-ctx").creator_id == superuser.pk
