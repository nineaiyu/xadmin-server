# -*- coding: utf-8 -*-
"""操作日志正文读侧收敛测试（列表/详情同口径）。

retrieve 与导出经 ``OperationLogSerializer`` 输出——正文（body /
response_result）此前在该口径下不截断、不重脱敏，持 retrieve/export 权限的
角色可整包拉取全站请求/响应正文（存量行还可能带着旧写入口径漏掩的明文）。
收敛后读侧与列表同级：重跑脱敏 + 有界预览；changes（变更历史渲染数据）
保持全量不裁剪。
"""

import json

import pytest

from audit.models import OperationLog
from audit.serializers.log import (
    OPERATION_LOG_LIST_PREVIEW_MAX,
    OperationLogListSerializer,
    OperationLogSerializer,
)

pytestmark = pytest.mark.django_db

OPERATION_URL = "/api/audit/logs/operation"

SECRET = "scim-bearer-secret-9f2c"


def _make_row(**overrides):
    """构造带长正文与「旧口径漏掩敏感值」的日志行（模拟存量数据）。"""
    long_body = json.dumps(
        {
            "key": "SCIM_TOKEN",
            "value": SECRET,  # 修复前的存量行：value 明文落库
            "note": "x" * 800,
        }
    )
    long_response = json.dumps({"code": 1000, "data": {"blob": "y" * 900}})
    row = OperationLog.objects.create(
        module="测试模块",
        path="/api/system/config/system",
        method="POST",
        body=long_body,
        response_result=long_response,
        changes=json.dumps([{"field": "value", "old": "a", "new": "b"}]),
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


class TestSerializerBodyConvergence:
    def test_retrieve_serializer_truncates_body(self):
        row = _make_row()
        data = OperationLogSerializer(row).data
        assert len(data["body"]) == OPERATION_LOG_LIST_PREVIEW_MAX, "retrieve 口径与列表同级截断"

    def test_retrieve_serializer_masks_legacy_plaintext(self):
        """存量行里 value 键的明文凭据在读侧重脱敏后不外发。"""
        row = _make_row()
        data = OperationLogSerializer(row).data
        assert SECRET not in data["body"]

    def test_value_only_config_body_masked_on_read(self):
        """存量行（仅 value、无 key 载体）在配置写入端点按值语义兜底掩码。"""
        row = _make_row(body=json.dumps({"value": SECRET, "is_active": True}))
        data = OperationLogSerializer(row).data
        assert SECRET not in data["body"]

    def test_value_only_body_not_masked_for_other_paths(self):
        """非配置写入端点不对普通 value 字段误伤。"""
        row = _make_row(body=json.dumps({"value": SECRET}), path="/api/demo/book")
        data = OperationLogSerializer(row).data
        assert SECRET in data["body"]

    def test_response_result_truncated(self):
        row = _make_row()
        data = OperationLogSerializer(row).data
        assert len(data["response_result"]) == OPERATION_LOG_LIST_PREVIEW_MAX

    def test_changes_kept_full(self):
        """changes 是「变更历史」弹窗的渲染数据：保持全量不裁剪。"""
        row = _make_row()
        data = OperationLogSerializer(row).data
        assert json.dumps([{"field": "value", "old": "a", "new": "b"}]) == data["changes"]

    def test_short_body_untouched(self):
        """短正文（未达上限）原样返回，不因收敛丢信息。"""
        row = _make_row()
        row.body = json.dumps({"key": "SITE_TITLE", "value": "xadmin"})
        data = OperationLogSerializer(row).data
        assert json.loads(data["body"]) == {"key": "SITE_TITLE", "value": "xadmin"}

    def test_truncated_json_falls_back_to_text(self):
        """落库截断导致的残缺 JSON（解析失败）按文本截断，不抛异常。"""
        row = _make_row()
        row.body = '{"blob": "' + "z" * 600  # 非 JSON
        data = OperationLogSerializer(row).data
        assert len(data["body"]) == OPERATION_LOG_LIST_PREVIEW_MAX

    def test_list_serializer_same_bounds(self):
        """列表口径不变：同样的截断上界（回归护栏）。"""
        row = _make_row()
        data = OperationLogListSerializer(row).data
        assert len(data["body"]) == OPERATION_LOG_LIST_PREVIEW_MAX
        assert SECRET not in data["body"]


class TestRetrieveEndpoint:
    def test_retrieve_response_has_no_full_body_nor_plaintext(self, auth_client):
        row = _make_row()
        resp = auth_client.get(f"{OPERATION_URL}/{row.pk}")
        assert resp.status_code == 200, resp.data
        data = resp.data["data"]
        assert len(data["body"]) <= OPERATION_LOG_LIST_PREVIEW_MAX
        assert SECRET not in str(resp.data)


class TestExportEndpoint:
    def test_export_csv_has_no_full_body_nor_plaintext(self, auth_client):
        """导出走 serializer_class：正文同样是重脱敏 + 有界预览，不构成整包外发出口。"""
        _make_row()
        resp = auth_client.get(f"{OPERATION_URL}/export-data", {"type": "csv"})
        assert resp.status_code == 200, getattr(resp, "data", None)
        raw = b"".join(resp.streaming_content) if getattr(resp, "streaming", False) else resp.content
        text = raw.decode("utf-8", errors="replace")
        assert SECRET not in text, "存量明文凭据不得经导出外发"
        assert "x" * (OPERATION_LOG_LIST_PREVIEW_MAX + 20) not in text, "正文列不得整段导出"
        assert "x" * 100 in text, "预览内仍应保留正文内容"
