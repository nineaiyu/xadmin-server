# -*- coding: utf-8 -*-
"""Webhook 事件契约（ADR-039 B4）：结构完备性 / payload 外壳 / 文档一致性 / 缺字段告警。"""

import importlib.util
import logging
from pathlib import Path

import pytest

from system.models.webhook import WebhookDelivery, WebhookSubscription
from system.utils.webhook import EVENT_CATALOG, emit_webhook_event, encrypt_secret

pytestmark = pytest.mark.django_db

VALID_TYPES = {"string", "integer", "number", "boolean", "object", "array"}


class TestEventContract:
    def test_every_event_has_valid_contract(self):
        for key, entry in EVENT_CATALOG.items():
            assert entry.get("label"), key
            assert isinstance(entry.get("version"), int) and entry["version"] >= 1, key
            fields = entry.get("fields")
            assert isinstance(fields, dict) and fields, key
            for name, spec in fields.items():
                assert spec.get("type", "string") in VALID_TYPES, (key, name)
                assert isinstance(spec.get("required"), bool), (key, name)
                assert spec.get("description"), (key, name)

    def test_event_keys_are_namespaced(self):
        for key in EVENT_CATALOG:
            assert "." in key and not key.startswith(".") and not key.endswith("."), key

    def test_required_fields_present_in_wiring_payloads(self):
        """关键信号源的 payload 必须覆盖契约 required 字段（真实接线点抽查）。"""
        from system.utils.approval import _emit_approval_event
        from system.utils.approval_flow import _emit_flow_event

        delivered = []

        def fake_emit(event, data):
            delivered.append((event, data))
            return 0

        import system.utils.approval as approval_module
        import system.utils.approval_flow as flow_module
        import system.utils.webhook as webhook_module

        original = webhook_module.emit_webhook_event
        webhook_module.emit_webhook_event = fake_emit
        try:

            class _Approval:
                pk = "11111111-1111-1111-1111-111111111111"
                module = "leave"
                path = "/system/leave"
                status = "PENDING"
                creator = None

            class _Instance:
                pk = "22222222-2222-2222-2222-222222222222"
                title = "请假申请"
                flow_name = "请假审批"
                status = "APPROVED"
                creator = None
                current_node = None
                reason = ""

            _emit_approval_event("approval.submitted", _Approval())
            _emit_flow_event("flow.approved", _Instance())
        finally:
            webhook_module.emit_webhook_event = original
            approval_module.emit_webhook_event = original
            flow_module.emit_webhook_event = original

        assert delivered, "未捕获到事件发射（接线点变更？）"
        for event, data in delivered:
            contract = EVENT_CATALOG[event]
            missing = [name for name, spec in contract["fields"].items() if spec.get("required") and name not in data]
            assert not missing, (event, missing)


class TestPayloadShell:
    def test_emitted_payload_contains_schema_version(self, superuser):
        WebhookSubscription.objects.create(
            name="契约订阅",
            url="http://127.0.0.1:9/hook",
            secret=encrypt_secret("whs-contract"),
            events=["webhook.ping"],
        )
        emit_webhook_event("webhook.ping", {"subscription": "契约订阅"})
        delivery = WebhookDelivery.objects.get(event="webhook.ping")
        assert delivery.payload["event"] == "webhook.ping"
        assert delivery.payload["schema_version"] == EVENT_CATALOG["webhook.ping"]["version"]
        assert delivery.payload["occurred_at"]
        assert delivery.payload["data"] == {"subscription": "契约订阅"}

    def test_missing_required_field_logs_warning(self, superuser, caplog):
        WebhookSubscription.objects.create(
            name="契约订阅2",
            url="http://127.0.0.1:9/hook",
            secret=encrypt_secret("whs-contract"),
            events=["webhook.ping"],
        )
        with caplog.at_level(logging.WARNING):
            emit_webhook_event("webhook.ping", {"unexpected": "x"})
        assert any("missing required fields" in record.message for record in caplog.records)


class TestEventDocs:
    def test_docs_match_catalog(self):
        script = Path(__file__).resolve().parents[3] / "scripts" / "gen_event_docs.py"
        spec = importlib.util.spec_from_file_location("gen_event_docs", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from django.utils.translation import override

        with override(None):
            expected = module.render()
        current = module.DOC_PATH.read_text(encoding="utf-8")
        assert current == expected, (
            "docs/open-platform/events.md 与 EVENT_CATALOG 漂移，请运行 scripts/gen_event_docs.py"
        )
