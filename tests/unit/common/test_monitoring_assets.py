# -*- coding: utf-8 -*-
"""监控参考栈资产：编排 / 抓取配置 / 告警规则 / 面板 / systemd 单元。

守护三类风险：
1. **指标名漂移**：告警规则与面板的表达式必须只用代码里真实存在的指标
   （名字改了而规则没改 = 静默失效的告警）；
2. **令牌与路径接线**：抓取配置的令牌文件、指标路径、探针目标必须与端点实现一致；
3. **资产缺失**：systemd 单元引用的脚本/环境文件必须在仓库中存在，文档索引已登记。
"""

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
MONITORING = ROOT / "utils" / "monitoring"

#: 指标名后缀：Prometheus 客户端为 Histogram/Counter 生成的派生系列
_SUFFIXES = ("_bucket", "_count", "_sum", "_total", "_created")


def _metric_names_in_code() -> set:
    """从 common/metrics.py 源码提取指标名（HELP/TYPE 声明即注册名）。"""
    source = (ROOT / "common" / "metrics.py").read_text(encoding="utf-8")
    return set(re.findall(r"xadmin_[a-z_]+", source))


def _base_name(name: str) -> str:
    for suffix in _SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _known_names() -> set:
    """代码里声明的指标名 + 其去掉派生后缀的基名（Histogram 的 _bucket/_count/_sum 等）。"""
    declared = _metric_names_in_code()
    return declared | {_base_name(name) for name in declared}


def _metric_names_in_text(text: str) -> set:
    """提取文本中引用的 xadmin 指标名（表达式 / JSON 面板 / 注释），归一为基名。"""
    return {_base_name(raw) for raw in re.findall(r"xadmin_[a-zA-Z_]+", text)}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestComposeAndScrapeConfig:
    def test_compose_is_valid_yaml_and_wires_assets(self):
        compose = yaml.safe_load(_read(MONITORING / "docker-compose.monitoring.yml"))
        services = compose["services"]
        assert set(services) == {"prometheus", "blackbox", "grafana"}
        # 抓取配置与令牌文件挂载（令牌独立文件、只读、不进仓库）
        mounts = " ".join(services["prometheus"]["volumes"])
        for asset in ("prometheus.yml", "alerts.yml", "metrics_token"):
            assert asset in mounts
        # 端口只发布到回环：参考栈默认不对公网暴露
        for service in ("prometheus", "grafana"):
            for mapping in services[service]["ports"]:
                assert str(mapping).startswith("127.0.0.1:"), mapping

    def test_prometheus_scrapes_metrics_endpoint_and_probe(self):
        config = yaml.safe_load(_read(MONITORING / "prometheus.yml"))
        scrape = {job["job_name"]: job for job in config["scrape_configs"]}
        app = scrape["xadmin-app"]
        # 指标端点（common/urls.py）+ Bearer 令牌文件
        assert app["metrics_path"] == "/api/common/api/metrics"
        assert app["authorization"]["credentials_file"] == "/etc/prometheus/metrics_token"
        assert config["rule_files"] == ["/etc/prometheus/alerts.yml"]
        # 健康探针：走 blackbox，目标为 healthz
        health = scrape["xadmin-health"]
        assert "/api/common/api/health" in " ".join(health["static_configs"][0]["targets"])
        replacements = {item.get("target_label"): item.get("replacement") for item in health["relabel_configs"]}
        assert replacements.get("__address__") == "blackbox:9115"

    def test_blackbox_module_declared(self):
        config = yaml.safe_load(_read(MONITORING / "blackbox.yml"))
        assert config["modules"]["http_2xx"]["prober"] == "http"


class TestAlertRules:
    def test_rules_cover_four_slo_items(self):
        config = yaml.safe_load(_read(MONITORING / "alerts.yml"))
        rules = [rule for group in config["groups"] for rule in group["rules"]]
        names = {rule["alert"] for rule in rules}
        assert {
            "XadminAvailabilityLow",  # HTTP 可用性
            "XadminApiLatencyHigh",  # API P95
            "XadminTaskFailureRateHigh",  # 任务成功率
            "XadminQueueBacklogHigh",  # 队列积压
        } <= names
        # 每条规则必须有 severity 与可读注解（值班侧才可行动）
        for rule in rules:
            assert rule["labels"]["severity"] in {"critical", "warning", "info"}
            assert rule["annotations"]["summary"]

    def test_rule_expressions_use_existing_metrics(self):
        """指标名漂移守护：规则里引用的指标必须在代码中真实存在。"""
        available = _known_names()
        used = _metric_names_in_text(_read(MONITORING / "alerts.yml"))
        # 注释里的观察项也一并校验（开启时即可用）
        missing = {name for name in used if name not in available}
        assert not missing, f"alerts.yml 引用了不存在的指标：{sorted(missing)}"

    def test_dashboard_expressions_use_existing_metrics(self):
        available = _known_names()
        dashboard = _read(MONITORING / "grafana/dashboards/xadmin-overview.json")
        missing = {name for name in _metric_names_in_text(dashboard) if name not in available}
        assert not missing, f"面板引用了不存在的指标：{sorted(missing)}"
        # uid 与数据源装配同源（否则面板打开即"数据源不存在"）
        assert json.loads(dashboard)["uid"] == "xadmin-overview"
        datasource = yaml.safe_load(_read(MONITORING / "grafana/provisioning/datasources/prometheus.yml"))
        assert datasource["datasources"][0]["uid"] == "prometheus"


class TestSystemdUnits:
    UNITS = (
        "xadmin-oom-alert.service",
        "xadmin-slo-snapshot.service",
        "xadmin-slo-snapshot.timer",
        "xadmin-prometheus-alert-bridge.service",
        "xadmin-prometheus-alert-bridge.timer",
    )

    #: 常驻/定时器单元需要 enable（[Install]）；oneshot 服务由 timer 触发，不需要
    INSTALLABLE = (
        "xadmin-oom-alert.service",
        "xadmin-slo-snapshot.timer",
        "xadmin-prometheus-alert-bridge.timer",
    )

    @pytest.mark.parametrize("unit", UNITS)
    def test_unit_exists_with_required_sections(self, unit):
        text = _read(MONITORING / "systemd" / unit)
        assert "[Unit]" in text, unit
        if unit.endswith(".timer"):
            assert "[Timer]" in text, unit
            assert "OnCalendar" in text or "OnUnitActiveSec" in text, unit
        else:
            assert "[Service]" in text, unit
        if unit in self.INSTALLABLE:
            assert "[Install]" in text, unit

    def test_services_reference_repo_scripts_and_env_file(self):
        cases = {
            "xadmin-oom-alert.service": "utils/oom_alert.sh",
            "xadmin-slo-snapshot.service": "utils/slo_snapshot_cron.sh",
            "xadmin-prometheus-alert-bridge.service": "scripts/prometheus_alert_bridge.py",
        }
        for unit, script in cases.items():
            text = _read(MONITORING / "systemd" / unit)
            assert script in text, unit
            assert (ROOT / script).is_file(), script
            assert "EnvironmentFile=/etc/xadmin/ops-alert.env" in text, unit

    def test_env_example_contains_all_agent_variables(self):
        text = _read(MONITORING / "systemd" / "ops-alert.env.example")
        for key in (
            "OPS_ALERT_URL",
            "OPS_ALERT_TOKEN",
            "METRICS_URL",
            "METRICS_TOKEN",
            "SLO_SNAPSHOT_FILE",
            "PROMETHEUS_URL",
            "PROMETHEUS_ALERT_STATE",
            "PROMETHEUS_ALERT_REPEAT_SECONDS",
        ):
            assert key in text, key


class TestDocsAndSecrets:
    def test_doc_registered_in_index(self):
        assert "ops/monitoring-stack.md" in _read(ROOT / "docs" / "README.md")
        doc = _read(ROOT / "docs" / "ops" / "monitoring-stack.md")
        # 文档必须覆盖：启动方式、令牌来源、告警投递链路、边界
        for token in (
            "docker-compose.monitoring.yml",
            "METRICS_TOKEN",
            "prometheus_alert_bridge.py",
            "OPS_ALERT_TOKEN",
        ):
            assert token in doc, token

    def test_metrics_token_is_gitignored(self):
        gitignore = _read(ROOT / ".gitignore")
        assert "utils/monitoring/metrics_token" in gitignore
        # 只提交示例文件，真实令牌不入库
        assert (MONITORING / "metrics_token.example").is_file()

    def test_observability_doc_points_to_stack(self):
        doc = _read(ROOT / "docs/ops/observability.md")
        assert "monitoring-stack.md" in doc
