# -*- coding: utf-8 -*-
"""ops/init_config.sh 配置引导脚本测试（源码部署的 config.yml + .env 生成）。

脚本是 bash，测试方式与 test_db_backup_script.py 相同：临时目录搭最小环境
（真实脚本 + config_example.yml + compose_env.sh），跑多组场景断言副作用：

- 新装：config.yml 生成且 SECRET_KEY / DB_PASSWORD / REDIS_PASSWORD 为非空随机值，
  .env 与 config.yml 两处密码一致（compose `:?` 强制注入因此可过），权限 0600；
- 幂等：重复执行不改变任何已生成值；
- 已有自定义值保留：用户手改的密码不被覆盖，且同步进 .env；
- config.yml 重建：删除后重跑，凭据从既有 .env 回读（不产生与 .env 不一致的新密码）；
- 注释行不被误改（`#DB_PASSWORD:` 这类注释保持原样）；
- --no-secrets：只同步 .env，不写密钥。
"""

import os
import stat
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SCRIPT = os.path.join(SCRIPT_DIR, "ops", "init_config.sh")
REPO = SCRIPT_DIR


def _read(path: str) -> str:
    with open(path) as fh:
        return fh.read()


def _yaml_value(config_text: str, key: str) -> str:
    for line in config_text.splitlines():
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip()
    return ""


def _env_value(env_text: str, key: str) -> str:
    for line in env_text.splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    return ""


def run_init_config(tmp_path, *args: str) -> subprocess.CompletedProcess:
    """在 tmp_path 搭最小环境（脚本 + 模板 + compose_env.sh）并执行。"""
    ops_dir = tmp_path / "ops"
    ops_dir.mkdir(exist_ok=True)
    for name in ("init_config.sh", "compose_env.sh"):
        (ops_dir / name).write_bytes(open(os.path.join(REPO, "ops", name), "rb").read())
    (tmp_path / "config_example.yml").write_bytes(open(os.path.join(REPO, "config_example.yml"), "rb").read())
    return subprocess.run(
        ["bash", "ops/init_config.sh", *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_fresh_deploy_generates_secrets_and_env(tmp_path):
    result = run_init_config(tmp_path)
    assert result.returncode == 0, result.stderr

    config = _read(tmp_path / "config.yml")
    env = _read(tmp_path / ".env")
    secret_key = _yaml_value(config, "SECRET_KEY")
    db_password = _yaml_value(config, "DB_PASSWORD")
    redis_password = _yaml_value(config, "REDIS_PASSWORD")

    assert len(secret_key) == 49
    assert db_password and redis_password
    # config.yml 与 .env 两处一致（compose :? 强制注入靠 .env，应用侧读 config.yml）
    assert _env_value(env, "DB_PASSWORD") == db_password
    assert _env_value(env, "REDIS_PASSWORD") == redis_password
    # 凭据缓存 0600
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600
    # 注释模板行不被误改
    assert "#DB_PASSWORD: <your-db-password>" in config


def test_rerun_is_idempotent(tmp_path):
    run_init_config(tmp_path)
    before = _read(tmp_path / "config.yml")
    result = run_init_config(tmp_path)
    assert result.returncode == 0, result.stderr
    assert _read(tmp_path / "config.yml") == before


def test_existing_custom_values_are_preserved(tmp_path):
    run_init_config(tmp_path)
    config_path = tmp_path / "config.yml"
    config = _read(config_path)
    config = config.replace(_yaml_value(config, "DB_PASSWORD"), "my-own-db-pass", 1)
    config = config.replace(_yaml_value(config, "REDIS_PASSWORD"), "my-own-redis-pass", 1)
    config_path.write_text(config)

    result = run_init_config(tmp_path)
    assert result.returncode == 0, result.stderr
    config_after = _read(config_path)
    assert _yaml_value(config_after, "DB_PASSWORD") == "my-own-db-pass"
    assert _yaml_value(config_after, "REDIS_PASSWORD") == "my-own-redis-pass"
    env = _read(tmp_path / ".env")
    assert _env_value(env, "DB_PASSWORD") == "my-own-db-pass"
    assert _env_value(env, "REDIS_PASSWORD") == "my-own-redis-pass"


def test_recreated_config_reads_back_from_env(tmp_path):
    run_init_config(tmp_path)
    env_before = _read(tmp_path / ".env")
    (tmp_path / "config.yml").unlink()

    result = run_init_config(tmp_path)
    assert result.returncode == 0, result.stderr
    # .env 凭据保持不变（不产生与既有 .env 不一致的新密码）
    assert _read(tmp_path / ".env") == env_before
    config = _read(tmp_path / "config.yml")
    assert _yaml_value(config, "DB_PASSWORD") == _env_value(env_before, "DB_PASSWORD")
    assert _yaml_value(config, "REDIS_PASSWORD") == _env_value(env_before, "REDIS_PASSWORD")


def test_no_secrets_only_syncs_env(tmp_path):
    result = run_init_config(tmp_path, "--no-secrets")
    assert result.returncode == 0, result.stderr
    config = _read(tmp_path / "config.yml")
    # config.yml 从模板复制而来，SECRET_KEY / DB_PASSWORD 保持空值
    assert _yaml_value(config, "SECRET_KEY") == ""
    assert _yaml_value(config, "DB_PASSWORD") == ""
    # .env 仍会生成（compose 缺凭据拒启），值为随机非空
    env = _read(tmp_path / ".env")
    assert _env_value(env, "DB_PASSWORD")
    assert _env_value(env, "REDIS_PASSWORD")
