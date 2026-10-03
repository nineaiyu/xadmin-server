# -*- coding: utf-8 -*-
"""横向扩展资产：scale overlay 的必需项 / nginx 后端两形态 / 文档登记。

这些是部署期资产（compose + nginx conf），用源码级断言守护「必需项被误删」或
「两形态漂移」；compose 的可合并性另有一条 docker CLI 实测用例（无 CLI 时跳过）。
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _compose_text() -> str:
    return (ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def _service_block(name: str, next_name: str) -> str:
    text = _compose_text()
    return text.split(f"\n  {name}:")[1].split(f"\n  {next_name}:")[0]


def _gunicorn_cmd() -> str:
    from common.management.commands.services.services.gunicorn import GunicornService

    return " ".join(GunicornService(name="gunicorn", worker_gunicorn=2).cmd)


class TestScaleOverlay:
    def test_scale_overlay_declares_required_changes(self):
        text = (ROOT / "docker-compose.scale.yml").read_text(encoding="utf-8")
        # ① 去 container_name：与 --scale 互斥
        assert "container_name: !reset" in text
        # ② 迁移只跑一次：web 侧关闭启动自动迁移
        assert 'AUTO_MIGRATE: "false"' in text
        # ③ nginx 换多后端 upstream
        assert "xadmin-backend.multi.conf" in text

    def test_base_compose_declares_beat_singleton(self):
        """beat 单例声明（重复 beat 会重复投递周期任务）。"""
        text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        beat_block = text.split("celery-beat:")[1]
        assert "replicas: 1" in beat_block

    def test_base_compose_mounts_single_backend_conf(self):
        text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        assert "./utils/xadmin-backend.single.conf:/etc/nginx/xadmin-backend.conf:r" in text

    def test_nginx_backend_forms(self):
        """主配置只 include；单/多副本形态各自独立（同一挂载点切换）。"""
        main = (ROOT / "utils/nginx.conf").read_text(encoding="utf-8")
        assert "include /etc/nginx/xadmin-backend.conf;" in main

        single = (ROOT / "utils/xadmin-backend.single.conf").read_text(encoding="utf-8")
        assert "set $xadmin_api_backend server:8896;" in single
        # 单副本不得引入配置加载期解析（注释提及 upstream 不算指令）
        directives = "\n".join(line for line in single.splitlines() if not line.lstrip().startswith("#"))
        assert "upstream" not in directives

        multi = (ROOT / "utils/xadmin-backend.multi.conf").read_text(encoding="utf-8")
        assert "zone xadmin_api 64k;" in multi  # resolve 的前置条件
        assert "server server:8896 resolve;" in multi
        assert "proxy_pass xadmin_api;" in multi

    def test_scale_out_doc_registered(self):
        assert "ops/scale-out.md" in (ROOT / "docs/README.md").read_text(encoding="utf-8")


class TestZeroDowntimeAssets:
    """零停机发布：宽限期与启动宽限的关系、runbook 登记。"""

    def test_server_grace_period_exceeds_gunicorn_graceful_timeout(self, capsys):
        """停止宽限期必须大于 gunicorn 优雅退出时长，否则 docker 默认 10s 就 SIGKILL。"""
        block = _service_block("server", "celery-worker")
        period = int(re.search(r"stop_grace_period: (\d+)s", block).group(1))
        timeout = int(re.search(r"--graceful-timeout (\d+)", _gunicorn_cmd()).group(1))
        assert period > timeout

    def test_worker_grace_periods_allow_warm_shutdown(self):
        """worker/heavy 宽限期覆盖在途任务（heavy 更长：导出/报表单任务数分钟）。"""
        worker = _service_block("celery-worker", "celery-heavy")
        heavy = _service_block("celery-heavy", "celery-beat")
        worker_period = int(re.search(r"stop_grace_period: (\d+)s", worker).group(1))
        heavy_period = int(re.search(r"stop_grace_period: (\d+)s", heavy).group(1))
        assert worker_period >= 120
        assert heavy_period >= worker_period

    def test_healthcheck_start_period(self):
        """启动期不误判 unhealthy（web 启动含 migrate/collectstatic/编译文案）。"""
        assert "start_period: 90s" in _service_block("server", "celery-worker")

    def test_blue_green_doc_registered(self):
        assert "ops/blue-green.md" in (ROOT / "docs/README.md").read_text(encoding="utf-8")
        doc = (ROOT / "docs/ops/blue-green.md").read_text(encoding="utf-8")
        # 四条前置能力必须在文中出现（否则读者按缺件的流程操作）
        for asset in ("migrate", "xadmin-backend.multi.conf", "graceful-timeout", "start_period"):
            assert asset in doc
        assert "docker-compose.scale.yml" in doc


@pytest.mark.parametrize(
    "overlays",
    [
        ["-f", "docker-compose.yml"],
        ["-f", "docker-compose.yml", "-f", "docker-compose.prod.yml"],
        ["-f", "docker-compose.yml", "-f", "docker-compose.prod.yml", "-f", "docker-compose.scale.yml"],
    ],
)
def test_compose_config_merges(overlays):
    """compose 合并校验（docker CLI 可用时执行；只需 CLI，不需要 daemon）。"""
    if shutil.which("docker") is None:
        pytest.skip("docker CLI 不可用")
    probe = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, cwd=ROOT, check=False)
    if probe.returncode != 0:
        pytest.skip("docker compose 不可用")

    env = dict(os.environ, DB_PASSWORD="compose-check", REDIS_PASSWORD="compose-check")
    result = subprocess.run(
        ["docker", "compose", *overlays, "config"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "services:" in result.stdout
