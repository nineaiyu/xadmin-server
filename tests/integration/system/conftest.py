# -*- coding: utf-8 -*-
"""system 域集成测试的共享夹具：旧结构样本库（真·原地升级的验证靶）。"""

import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest
from django.conf import settings

from ops.legacy import schema as legacy_schema

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_SCRIPT = REPO_ROOT / "ops" / "legacy_sample_db.py"
LEGACY_TAG = "v4.2.5"


def _params() -> dict:
    db = settings.DATABASES["default"]
    return {
        "host": db["HOST"],
        "port": str(db["PORT"]),
        "user": db["USER"],
        "password": db["PASSWORD"] or "",
        "name": f"{db['NAME']}_legacy_sample",
    }


def sample_dsn() -> str:
    params = _params()
    return (
        f"postgresql://{quote(params['user'])}:{quote(params['password'])}"
        f"@{params['host']}:{params['port']}/{params['name']}"
    )


def build_sample(meta_path: Path, tag: str = LEGACY_TAG) -> str:
    """用 ``ops/legacy_sample_db.py`` 建指定 tag 的样本库（含数据）；返回 dsn。"""
    result = subprocess.run(
        [
            sys.executable,
            str(SAMPLE_SCRIPT),
            "--tag",
            tag,
            "--dsn",
            sample_dsn(),
            "--with-data",
            "--meta-out",
            str(meta_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:  # pragma: no cover - 环境问题（无 PG / 无建库权限）
        pytest.skip(f"样本库生成失败：{result.stdout}\n{result.stderr}")
    return sample_dsn()


def drop_sample() -> None:
    subprocess.run(
        [sys.executable, str(SAMPLE_SCRIPT), "--dsn", sample_dsn(), "--drop"],
        capture_output=True,
        text=True,
        check=False,
    )


def _require_history() -> None:
    if not legacy_schema.load_tag_models(LEGACY_TAG):
        pytest.skip(f"仓库内没有 {LEGACY_TAG} 的迁移历史（单仓检出）")


@pytest.fixture(scope="module")
def legacy_sample(tmp_path_factory):
    """只读场景：整个模块共用一个样本库（建库成本 ~10s）。"""
    _require_history()
    meta_path = tmp_path_factory.mktemp("legacy-sample") / "meta.json"
    dsn = build_sample(meta_path)
    yield dsn, json.loads(meta_path.read_text(encoding="utf-8"))
    drop_sample()


@pytest.fixture
def sample_factory(tmp_path):
    """按 tag 现建样本库的工厂（用例结束统一清理），用于多版本家族回归。"""

    def _build(tag: str):
        meta_path = tmp_path / f"meta-{tag}.json"
        dsn = build_sample(meta_path, tag)
        return dsn, json.loads(meta_path.read_text(encoding="utf-8"))

    yield _build
    drop_sample()


@pytest.fixture
def legacy_sample_fresh(tmp_path):
    """会被改写的场景：每个用例一个全新样本库（结构对齐会改表名）。"""
    _require_history()
    meta_path = tmp_path / "meta.json"
    dsn = build_sample(meta_path)
    yield dsn, json.loads(meta_path.read_text(encoding="utf-8"))
    drop_sample()
