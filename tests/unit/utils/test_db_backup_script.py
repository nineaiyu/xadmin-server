# -*- coding: utf-8 -*-
"""utils/db_backup.sh 备份脚本测试（数据库包 + 媒体目录 + 异地副本）。

脚本是 bash，测试方式为「stub 命令 + 真实脚本」：在临时目录造假的
`pg_dump` / `rclone` 前置到 PATH，用 BACKUP_ONCE=1 跑单轮，断言产物与副作用：

- 数据库包 + .sha256 校验和 sidecar
- 媒体目录打包开关（BACKUP_MEDIA）
- 异地副本同步（local / rclone 分派）
- 保留期滚动清理
- pg_dump 失败时不残留半成品
"""

import hashlib
import os
import subprocess

import pytest

SCRIPT = (
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    + "/utils/db_backup.sh"
)

PG_DUMP_STUB = """#!/bin/sh
if [ "${STUB_PG_DUMP_FAIL:-0}" = "1" ]; then
  echo "pg_dump: simulated failure" >&2
  exit 1
fi
echo "-- fake dump for ${PGDATABASE}"
echo "COPY public.fake (id) FROM stdin;"
"""

RSYNC_STUB = """#!/bin/sh
echo "$@" >> "${STUB_RSYNC_LOG:?}"
"""

RCLONE_STUB = """#!/bin/sh
echo "$@" >> "${STUB_RCLONE_LOG:?}"
"""


def write_executable(path: os.PathLike, body: str) -> None:
    with open(path, "w") as fh:
        fh.write(body)
    os.chmod(path, 0o755)


@pytest.fixture()
def sandbox(tmp_path):
    """构造脚本运行环境：stub 命令 + 备份目录 + 媒体目录。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(bin_dir / "pg_dump", PG_DUMP_STUB)
    write_executable(bin_dir / "rsync", RSYNC_STUB)
    write_executable(bin_dir / "rclone", RCLONE_STUB)

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    media_dir = tmp_path / "media" / "upload"
    media_dir.mkdir(parents=True)
    (media_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    remote_dir = tmp_path / "remote"
    remote_dir.mkdir()

    return {
        "tmp_path": tmp_path,
        "bin_dir": bin_dir,
        "backup_dir": backup_dir,
        "media_dir": media_dir,
        "remote_dir": remote_dir,
    }


def run_script(sandbox, **overrides):
    env = dict(os.environ)
    env["PATH"] = f"{sandbox['bin_dir']}{os.pathsep}{env['PATH']}"
    env.update(
        {
            "BACKUP_DIR": str(sandbox["backup_dir"]),
            "BACKUP_ONCE": "1",
            "PGDATABASE": "xadmin",
            "PGHOST": "postgresql",
            "PGPORT": "5432",
            "PGUSER": "server",
            "KEEP_DAYS": "7",
            "BACKUP_MEDIA": "true",
            "MEDIA_DIR": str(sandbox["media_dir"]),
        }
    )
    env.update({k: str(v) for k, v in overrides.items()})
    return subprocess.run(
        ["bash", SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def read_checksum(archive) -> str:
    """返回 .sha256 sidecar 中记录的哈希值。"""
    sidecar = f"{archive}.sha256"
    with open(sidecar) as fh:
        return fh.read().split()[0]


def sha256_of(path) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class TestDbBackupScript:
    def test_script_exists(self):
        assert os.path.isfile(SCRIPT)

    def test_single_run_produces_archive_and_checksum(self, sandbox):
        result = run_script(sandbox)
        assert result.returncode == 0, result.stderr

        archives = sorted(sandbox["backup_dir"].glob("*.sql.gz"))
        assert len(archives) == 1
        archive = archives[0]
        assert archive.stat().st_size > 0
        assert os.path.isfile(f"{archive}.sha256")
        assert read_checksum(archive) == sha256_of(archive)

    def test_latest_backup_marker_written(self, sandbox):
        assert run_script(sandbox).returncode == 0
        marker = sandbox["backup_dir"] / ".latest_backup"
        assert marker.is_file()
        lines = [line for line in marker.read_text().splitlines() if line]
        assert lines and lines[0].endswith(".sql.gz")
        assert os.path.isfile(lines[0])

    def test_media_backup_enabled(self, sandbox):
        assert run_script(sandbox, BACKUP_MEDIA="true").returncode == 0
        media = sorted(sandbox["backup_dir"].glob("*.media.tar.gz"))
        assert len(media) == 1
        assert read_checksum(media[0]) == sha256_of(media[0])

    def test_media_backup_disabled(self, sandbox):
        assert run_script(sandbox, BACKUP_MEDIA="false").returncode == 0
        assert list(sandbox["backup_dir"].glob("*.media.tar.gz")) == []

    def test_media_dir_missing_only_warns(self, sandbox):
        result = run_script(sandbox, MEDIA_DIR=str(sandbox["tmp_path"] / "nope"))
        assert result.returncode == 0
        assert "媒体目录不存在" in result.stderr
        assert len(list(sandbox["backup_dir"].glob("*.sql.gz"))) == 1

    def test_remote_local_sync(self, sandbox):
        result = run_script(
            sandbox,
            BACKUP_REMOTE_TYPE="local",
            BACKUP_REMOTE_TARGET=str(sandbox["remote_dir"]),
        )
        assert result.returncode == 0
        remote_files = {p.name for p in sandbox["remote_dir"].iterdir()}
        assert any(name.endswith(".sql.gz") for name in remote_files)
        assert any(name.endswith(".sha256") for name in remote_files)

    def test_remote_rclone_dispatch(self, sandbox):
        log = sandbox["tmp_path"] / "rclone.log"
        result = run_script(
            sandbox,
            BACKUP_REMOTE_TYPE="rclone",
            BACKUP_REMOTE_TARGET="oss:xadmin-backup",
            STUB_RCLONE_LOG=str(log),
        )
        assert result.returncode == 0
        logged = log.read_text()
        assert "oss:xadmin-backup" in logged
        assert ".sql.gz" in logged

    def test_remote_disabled_by_default(self, sandbox):
        assert run_script(sandbox).returncode == 0
        assert list(sandbox["remote_dir"].iterdir()) == []

    def test_retention_prunes_expired_packages(self, sandbox):
        assert run_script(sandbox).returncode == 0
        stale = sandbox["backup_dir"] / "xadmin_20260101_000000.sql.gz"
        stale.write_bytes(b"x")
        os.utime(stale, (0, 0))
        write_executable(sandbox["backup_dir"] / "xadmin_20260101_000000.sql.gz.sha256", "")
        os.utime(f"{stale}.sha256", (0, 0))

        assert run_script(sandbox).returncode == 0
        # 过期包被清理；新一轮备份（秒级时间戳可能与上一轮不同名）必须保留
        remaining = list(sandbox["backup_dir"].glob("*.sql.gz"))
        assert stale not in remaining
        assert remaining
        assert not (sandbox["backup_dir"] / f"{stale.name}.sha256").exists()

    def test_remote_retention_prunes_expired(self, sandbox):
        result = run_script(
            sandbox,
            BACKUP_REMOTE_TYPE="local",
            BACKUP_REMOTE_TARGET=str(sandbox["remote_dir"]),
        )
        assert result.returncode == 0
        stale = sandbox["remote_dir"] / "xadmin_20260101_000000.sql.gz"
        stale.write_bytes(b"x")
        os.utime(stale, (0, 0))

        assert (
            run_script(
                sandbox,
                BACKUP_REMOTE_TYPE="local",
                BACKUP_REMOTE_TARGET=str(sandbox["remote_dir"]),
            ).returncode
            == 0
        )
        assert not stale.exists()

    def test_failed_dump_leaves_no_partial(self, sandbox):
        result = run_script(sandbox, STUB_PG_DUMP_FAIL="1")
        # 单次模式（演练/cron）必须把失败暴露成退出码，否则调度侧无法感知
        assert result.returncode == 1
        assert "backup FAILED" in result.stderr
        assert list(sandbox["backup_dir"].glob("*.sql.gz")) == []
        assert list(sandbox["backup_dir"].glob("*.tmp")) == []
