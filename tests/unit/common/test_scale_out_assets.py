# -*- coding: utf-8 -*-
"""横向扩展资产（P1-33）：scale overlay 的必需项 / nginx 后端两形态 / 文档登记。

这些是部署期资产（compose + nginx conf），用源码级断言守护「必需项被误删」或
「两形态漂移」；compose 的可合并性另有一条 docker CLI 实测用例（无 CLI 时跳过）。
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


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
