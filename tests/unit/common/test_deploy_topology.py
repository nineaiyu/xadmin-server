# -*- coding: utf-8 -*-
"""部署拓扑资产（独立盘迁移 / 生产强制异地副本、依赖条件）。

这些是部署期资产（compose + 备份脚本入口），用源码级断言守护
「可迁移性被误删」与「依赖语义回退」；宿主路径插值另有 docker CLI 实测
（只需 CLI、不需要 daemon）。
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _compose_text() -> str:
    return (ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def _server_block() -> str:
    return _compose_text().split("\n  server:")[1].split("\n  celery-worker:")[0]


class TestBackupTopology:
    """归档/异地副本的宿主路径可由环境变量迁移到独立盘，容器内路径不变。"""

    def test_pitr_archive_host_path_overridable(self):
        text = _compose_text()
        assert "${PITR_ARCHIVE_DIR:-" in text
        # 容器内挂载点恒定（归档器/恢复脚本依赖它）
        assert ":/var/lib/postgresql/archive" in text

    def test_backup_remote_host_path_overridable(self):
        text = _compose_text()
        assert "${BACKUP_REMOTE_DIR:-" in text
        assert ":/remote" in text

    def test_remote_required_passthrough_and_prod_default(self):
        """基础栈透传开关（默认 0 = 行为不变），生产 overlay 默认置 1。"""
        assert "BACKUP_REMOTE_REQUIRED: ${BACKUP_REMOTE_REQUIRED:-0}" in _compose_text()
        prod = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
        assert "BACKUP_REMOTE_REQUIRED: ${BACKUP_REMOTE_REQUIRED:-1}" in prod
        assert "db-backup:" in prod

    def test_backup_script_never_blocks_local_backup(self):
        """强制项只影响可见性：缺失时本地备份仍产出、单次模式非零退出。"""
        script = (ROOT / "utils/db_backup.sh").read_text(encoding="utf-8")
        assert "BACKUP_REMOTE_REQUIRED=${BACKUP_REMOTE_REQUIRED:-0}" in script
        assert "check_remote_required" in script
        # 自检不在备份主流程前置中断：do_backup 仍在 while 循环内无条件执行
        loop = script.split("while true; do")[1]
        assert "do_backup" in loop
        assert "exit 1" in script


class TestServiceDependencies:
    """只声明真实依赖并等依赖健康（nginx 无 healthcheck，排序语义弱）。"""

    def test_server_waits_for_datastores_healthy(self):
        block = _server_block()
        depends = block.split("depends_on:")[1].split("networks:")[0]
        assert "postgresql:" in depends
        assert "redis:" in depends
        assert depends.count("condition: service_healthy") == 2

    def test_server_does_not_depend_on_nginx(self):
        depends = _server_block().split("depends_on:")[1].split("networks:")[0]
        assert "nginx" not in depends


class TestDocsRegistered:
    def test_deployment_doc_documents_migration_switches(self):
        doc = (ROOT / "docs/ops/deployment.md").read_text(encoding="utf-8")
        for key in ("PITR_ARCHIVE_DIR", "BACKUP_REMOTE_DIR", "BACKUP_REMOTE_REQUIRED"):
            assert key in doc

    def test_pitr_doc_documents_archive_migration(self):
        doc = (ROOT / "docs/ops/pitr.md").read_text(encoding="utf-8")
        assert "PITR_ARCHIVE_DIR" in doc


@pytest.mark.parametrize(
    ("override_env", "expected_source"),
    [
        ({"PITR_ARCHIVE_DIR": "/mnt/pitr-archive"}, "/mnt/pitr-archive"),
        ({"BACKUP_REMOTE_DIR": "/mnt/backup-remote"}, "/mnt/backup-remote"),
    ],
)
def test_compose_interpolates_migration_env(override_env, expected_source):
    """docker CLI 可用时实测插值（独立盘只改宿主路径的承诺）。"""
    if shutil.which("docker") is None:
        pytest.skip("docker CLI 不可用")
    probe = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, cwd=ROOT, check=False)
    if probe.returncode != 0:
        pytest.skip("docker compose 不可用")

    env = dict(os.environ, DB_PASSWORD="compose-check", REDIS_PASSWORD="compose-check", **override_env)
    result = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.yml", "config"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert f"source: {expected_source}" in result.stdout
