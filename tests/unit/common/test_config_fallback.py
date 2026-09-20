# -*- coding: utf-8 -*-
"""配置回落与 SECRET_KEY 自动生成（开箱即用）守护测试。

背景：未创建 config.yml 时，``server/conf/manager.py`` 回落 ``config_example.yml``
并自动生成/持久化 SECRET_KEY（``data/.secret_key``）。本文件钉死该行为的关键边界：

1. 回落路径生效：自动生成 49 位密钥并持久化，二次加载复用同一密钥（重启一致性）；
2. 显式配置的 SECRET_KEY 不被覆盖，也不产生密钥文件；
3. 存在 config.yml 但未填 SECRET_KEY、非 DEBUG、未显式开启自动生成时保持为空
   （生产口径：交由 settings 校验拒绝启动，不得静默生成）；
4. DEBUG=true 时允许自动生成（本地开发免配置）。

SECRET_KEY 同时是 JWT 签名与字段级加密（signer）密钥，密钥稳定性由
``data/.secret_key`` 的持久化保证——本文件是「不自愈、只钉死」的守护。

实现说明：``tests/settings_test.py`` 在进程启动时把 ``ConfigManager.load_user_config``
替换为测试桩（隔离真实配置文件依赖），因此本文件通过 ``importlib.reload`` 取回
未打桩的类，覆盖真实装配链路；``load_from_object``（config.py 分支）的查找路径不受
root_path 控制，为隔离本机环境一并打桩。
"""

import importlib
import shutil
from pathlib import Path

import pytest

import server.conf.manager as manager_module

PROJECT_DIR = Path(__file__).resolve().parents[3]


@pytest.fixture()
def config_manager_cls(monkeypatch):
    """返回未打桩的 ConfigManager（模块重载取回真实实现）。"""
    module = importlib.reload(manager_module)
    monkeypatch.setattr(module.ConfigManager, "load_from_object", lambda self: False)
    return module.ConfigManager


def _prepare_example(root: Path):
    shutil.copy(PROJECT_DIR / "config_example.yml", root / "config_example.yml")


def test_fallback_generates_and_persists_secret_key(tmp_path, config_manager_cls):
    _prepare_example(tmp_path)
    config = config_manager_cls.load_user_config(root_path=str(tmp_path))

    assert config[manager_module.FALLBACK_FLAG] is True
    secret = config.get("SECRET_KEY")
    assert secret and len(secret) == manager_module.AUTO_SECRET_KEY_LENGTH

    secret_file = tmp_path / manager_module.AUTO_SECRET_KEY_FILE
    assert secret_file.is_file()
    assert secret_file.read_text(encoding="utf8").strip() == secret

    # 二次加载复用同一密钥（重启一致性；密钥变更会导致登录态失效与加密数据不可解）
    again = config_manager_cls.load_user_config(root_path=str(tmp_path))
    assert again.get("SECRET_KEY") == secret


def test_explicit_secret_key_not_overridden(tmp_path, config_manager_cls):
    _prepare_example(tmp_path)
    (tmp_path / "config.yml").write_text("SECRET_KEY: explicit-key-for-test\nDEBUG: false\n", encoding="utf8")

    config = config_manager_cls.load_user_config(root_path=str(tmp_path))

    assert config.get("SECRET_KEY") == "explicit-key-for-test"
    assert not (tmp_path / manager_module.AUTO_SECRET_KEY_FILE).exists()


def test_missing_secret_key_without_debug_or_switch_stays_empty(tmp_path, config_manager_cls):
    # 生产口径：显式提供 config.yml（未填 SECRET_KEY）、非 DEBUG、未开自动生成 → 保持为空
    (tmp_path / "config.yml").write_text("DEBUG: false\n", encoding="utf8")

    config = config_manager_cls.load_user_config(root_path=str(tmp_path))

    assert not config.get("SECRET_KEY")
    assert not (tmp_path / manager_module.AUTO_SECRET_KEY_FILE).exists()


def test_debug_enables_auto_generation(tmp_path, config_manager_cls):
    (tmp_path / "config.yml").write_text("DEBUG: true\n", encoding="utf8")

    config = config_manager_cls.load_user_config(root_path=str(tmp_path))

    assert config.get("SECRET_KEY")
    assert (tmp_path / manager_module.AUTO_SECRET_KEY_FILE).is_file()
