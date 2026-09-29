#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Prometheus 告警桥接（scripts/prometheus_alert_bridge.py）：
拉取 → firing 过滤 → 载荷映射 → 重复抑制与恢复重发 → 失败不推进状态。
"""

import datetime
import json

from scripts.prometheus_alert_bridge import (
    alert_fingerprint,
    build_alert_payload,
    build_parser,
    due_alerts,
    load_state,
    run_once,
    select_firing,
)

BASE = datetime.datetime(2026, 9, 29, 12, 0, 0)


def _alert(name="XadminQueueBacklogHigh", state="firing", **labels):
    payload = {
        "labels": {"alertname": name, "severity": "critical", "instance": "xadmin", **labels},
        "annotations": {"summary": f"{name} 触发", "description": "队列积压 > 500"},
        "state": state,
        "activeAt": "2026-09-29T11:58:00+08:00",
        "fingerprint": f"fp-{name}-{labels.get('queue', '')}",
    }
    return payload


class TestFiringSelection:
    def test_only_firing_selected(self):
        payload = {"data": {"alerts": [_alert(), _alert(name="Resolved", state="inactive")]}}
        firing = select_firing(payload)
        assert [alert["labels"]["alertname"] for alert in firing] == ["XadminQueueBacklogHigh"]

    def test_empty_response_is_safe(self):
        assert select_firing({}) == []
        assert select_firing({"data": {}}) == []

    def test_fingerprint_falls_back_to_labels(self):
        alert = _alert()
        alert.pop("fingerprint")
        assert alert_fingerprint(alert).startswith("XadminQueueBacklogHigh|")


class TestPayloadMapping:
    def test_payload_shape_matches_ops_alert_contract(self):
        payload = build_alert_payload(_alert(queue="heavy"), now=BASE)
        # 平台端点只认这五个字段（其余字段不会进站内信模板）
        assert set(payload) == {"source", "event", "host", "time", "detail"}
        assert payload["source"] == "prometheus"
        assert payload["event"] == "XadminQueueBacklogHigh [critical]"
        assert payload["host"] == "xadmin"
        assert payload["time"] == "2026-09-29T11:58:00+08:00"
        assert "队列积压 > 500" in payload["detail"]
        assert "queue=heavy" in payload["detail"]

    def test_detail_truncated(self):
        alert = _alert()
        alert["annotations"]["description"] = "x" * 5000
        assert len(build_alert_payload(alert, now=BASE)["detail"]) <= 2000

    def test_missing_labels_degrade(self):
        payload = build_alert_payload({"annotations": {}}, now=BASE)
        assert payload["event"] == "unknown"
        assert payload["host"] == "prometheus"
        assert payload["time"] == BASE.strftime("%Y-%m-%d %H:%M:%S")


class TestDedupe:
    def test_new_alert_is_due(self):
        alerts = [_alert()]
        assert due_alerts(alerts, {"fingerprints": {}}, BASE.timestamp(), 3600) == alerts

    def test_recent_notification_suppressed(self):
        state = {"fingerprints": {"fp-XadminQueueBacklogHigh-": BASE.isoformat(timespec="seconds")}}
        assert due_alerts([_alert()], state, BASE.timestamp() + 60, 3600) == []

    def test_repeat_after_window(self):
        state = {"fingerprints": {"fp-XadminQueueBacklogHigh-": BASE.isoformat(timespec="seconds")}}
        due = due_alerts([_alert()], state, BASE.timestamp() + 3601, 3600)
        assert len(due) == 1

    def test_corrupt_timestamp_treated_as_new(self):
        state = {"fingerprints": {"fp-XadminQueueBacklogHigh-": "not-a-time"}}
        assert len(due_alerts([_alert()], state, BASE.timestamp(), 3600)) == 1

    def test_load_state_tolerates_missing_and_corrupt(self, tmp_path):
        missing = tmp_path / "nope.json"
        assert load_state(str(missing)) == {"fingerprints": {}}
        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        assert load_state(str(broken)) == {"fingerprints": {}}


class TestRunOnce:
    def _wire(self, monkeypatch, alerts, post_results):
        """打桩：拉取固定告警，投递结果按序列出。"""
        import scripts.prometheus_alert_bridge as bridge

        monkeypatch.setattr(bridge, "fetch_json", lambda url, timeout=10: {"data": {"alerts": alerts}})
        calls = []

        def _post(url, token, payload, timeout=10):
            calls.append(payload)
            return post_results[min(len(calls) - 1, len(post_results) - 1)]

        monkeypatch.setattr(bridge, "post_ops_alert", _post)
        return calls

    def test_first_run_delivers_and_records(self, tmp_path, monkeypatch):
        calls = self._wire(monkeypatch, [_alert()], [True])
        state_file = tmp_path / "state.json"
        result = run_once("http://prom:9090", "http://x/ops-alert", "tok", str(state_file), now=BASE)
        assert result["delivered"] and not result["failed"]
        assert calls[0]["source"] == "prometheus"
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        assert "fp-XadminQueueBacklogHigh-" in saved["fingerprints"]

    def test_second_run_within_window_is_suppressed(self, tmp_path, monkeypatch):
        self._wire(monkeypatch, [_alert()], [True, True])
        state_file = tmp_path / "state.json"
        run_once("http://prom:9090", "http://x/ops-alert", "tok", str(state_file), now=BASE)
        result = run_once(
            "http://prom:9090",
            "http://x/ops-alert",
            "tok",
            str(state_file),
            now=BASE + datetime.timedelta(minutes=5),
        )
        assert result["due"] == 0 and result["delivered"] == []

    def test_resolved_alert_forgets_state(self, tmp_path, monkeypatch):
        self._wire(monkeypatch, [_alert()], [True, True])
        state_file = tmp_path / "state.json"
        run_once("http://prom:9090", "http://x/ops-alert", "tok", str(state_file), now=BASE)

        # 告警恢复：状态记录被清除
        import scripts.prometheus_alert_bridge as bridge

        monkeypatch.setattr(bridge, "fetch_json", lambda url, timeout=10: {"data": {"alerts": []}})
        run_once("http://prom:9090", "http://x/ops-alert", "tok", str(state_file), now=BASE)
        assert json.loads(state_file.read_text(encoding="utf-8"))["fingerprints"] == {}

        # 再次 firing：不等窗口立即通知
        monkeypatch.setattr(bridge, "fetch_json", lambda url, timeout=10: {"data": {"alerts": [_alert()]}})
        result = run_once(
            "http://prom:9090",
            "http://x/ops-alert",
            "tok",
            str(state_file),
            now=BASE + datetime.timedelta(minutes=6),
        )
        assert result["delivered"]

    def test_delivery_failure_does_not_advance_state(self, tmp_path, monkeypatch):
        self._wire(monkeypatch, [_alert()], [False, True])
        state_file = tmp_path / "state.json"
        result = run_once("http://prom:9090", "http://x/ops-alert", "tok", str(state_file), now=BASE)
        assert result["failed"] and not result["delivered"]
        assert json.loads(state_file.read_text(encoding="utf-8"))["fingerprints"] == {}

        # 失败不落状态：下一轮重试投递
        retry = run_once("http://prom:9090", "http://x/ops-alert", "tok", str(state_file), now=BASE)
        assert retry["delivered"]

    def test_dry_run_does_not_write_state(self, tmp_path, monkeypatch):
        self._wire(monkeypatch, [_alert()], [True])
        state_file = tmp_path / "state.json"
        result = run_once("http://prom:9090", "http://x/ops-alert", "tok", str(state_file), dry_run=True, now=BASE)
        assert result["delivered"]
        assert not state_file.exists()


class TestCli:
    def test_repeat_seconds_from_env(self, monkeypatch):
        monkeypatch.setenv("PROMETHEUS_ALERT_REPEAT_SECONDS", "60")
        args = build_parser().parse_args([])
        assert args.repeat_seconds == 60

    def test_missing_credentials_exit_2(self, monkeypatch, capsys):
        from scripts.prometheus_alert_bridge import main

        monkeypatch.delenv("OPS_ALERT_URL", raising=False)
        monkeypatch.delenv("OPS_ALERT_TOKEN", raising=False)
        assert main([]) == 2
        assert "缺少 OPS_ALERT_URL" in capsys.readouterr().err

    def test_exit_code_reflects_delivery(self, monkeypatch):
        """投递失败必须非零退出：systemd/cron 侧才能感知（下一轮会自动重试）。"""
        import scripts.prometheus_alert_bridge as bridge

        monkeypatch.setattr(
            bridge, "run_once", lambda *args, **kwargs: {"firing": 1, "due": 1, "delivered": [], "failed": ["fp"]}
        )
        from scripts.prometheus_alert_bridge import main

        assert main(["--ops-url", "http://x", "--token", "t"]) == 1

    def test_exit_code_zero_when_nothing_firing(self, monkeypatch):
        import scripts.prometheus_alert_bridge as bridge

        monkeypatch.setattr(
            bridge, "run_once", lambda *args, **kwargs: {"firing": 0, "due": 0, "delivered": [], "failed": []}
        )
        from scripts.prometheus_alert_bridge import main

        assert main(["--ops-url", "http://x", "--token", "t"]) == 0
