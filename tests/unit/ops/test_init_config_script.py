# -*- coding: utf-8 -*-
"""ops/init_config.sh 配置引导脚本测试（源码部署的 config.yml + .env 生成）。

脚本是 bash，测试方式与 test_db_backup_script.py 相同：临时目录搭最小环境
（真实脚本 + config_example.yml + compose_env.sh），跑多组场景断言副作用：

- 新装：config.yml 生成且 SECRET_KEY / DB_PASSWORD / REDIS_PASSWORD 为非空随机值，
  .env 与 config.yml 两处密码一致（compose `:?` 强制注入因此可过），权限 0600；
- 写入位置：模板里的占位注释行（#DB_PASSWORD: <your-db-password>）被原位解注释填值，
  密码落在对应配置段而非追加到文件末尾；「本地非 Docker 开发」备用注释块里的第二份
  占位保持注释态（不产生重复键）；其他纯注释键（#DEFAULT_CACHE_ID 等）不被误改；
  模板连占位都没有时退化为末尾追加（值仍生成、两处一致）；
- 幂等：重复执行不改变任何已生成值；
- 已有自定义值保留：用户手改的密码不被覆盖，且同步进 .env；
- config.yml 重建：删除后重跑，凭据从既有 .env 回读（不产生与 .env 不一致的新密码）；
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


def run_init_config(tmp_path, *args: str, template: str | None = None) -> subprocess.CompletedProcess:
    """在 tmp_path 搭最小环境（脚本 + 模板 + compose_env.sh）并执行。

    template 传入时使用自定义 config_example.yml（fallback 用例验证无占位模板），
    否则拷贝真实仓库模板。
    """
    ops_dir = tmp_path / "ops"
    ops_dir.mkdir(exist_ok=True)
    for name in ("init_config.sh", "compose_env.sh"):
        (ops_dir / name).write_bytes(open(os.path.join(REPO, "ops", name), "rb").read())
    if template is None:
        template = open(os.path.join(REPO, "config_example.yml"), "rb").read().decode()
    (tmp_path / "config_example.yml").write_text(template)
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


def _assert_inplace_fill(config_path):
    """密码应解注释填在模板对应配置段（原位），而不是追加到文件末尾。"""
    config = _read(config_path)
    lines = config.splitlines()
    db_idx = next(i for i, ln in enumerate(lines) if ln.startswith("DB_PASSWORD:"))
    redis_idx = next(i for i, ln in enumerate(lines) if ln.startswith("REDIS_PASSWORD:"))
    allowed_idx = next(i for i, ln in enumerate(lines) if ln.startswith("ALLOWED_HOSTS:"))
    xadmin_idx = next(i for i, ln in enumerate(lines) if ln.startswith("XADMIN_APPS:"))
    # DB_PASSWORD 在数据库配置段内（DB_DATABASE 行之后、本地非 Docker 注释块之前）
    assert "DB_DATABASE: xadmin" in lines[db_idx - 1], lines[db_idx - 1 : db_idx + 1]
    # REDIS_PASSWORD 在 Redis 段内（REDIS_PORT 之后、HTTP 绑定段之前）
    redis_port_idx = next(i for i, ln in enumerate(lines) if ln.startswith("REDIS_PORT:"))
    http_idx = next(i for i, ln in enumerate(lines) if ln.startswith("HTTP_BIND_HOST:"))
    assert redis_port_idx < redis_idx < http_idx
    # 全文件只出现一次（「本地非 Docker 开发」备用注释块的第二份占位保持注释态，
    # 不产生重复键——YAML 后者胜出会让改主段静默不生效）
    assert sum(1 for ln in lines if ln.startswith("DB_PASSWORD:")) == 1
    assert sum(1 for ln in lines if ln.startswith("REDIS_PASSWORD:")) == 1
    # 备用注释块里的占位仍是注释
    assert "# DB_PASSWORD: <your-db-password>" in config
    # 与 DB 无关的注释占位不被误改（模板尾部的注释键仍保持注释态）
    assert "#DEFAULT_CACHE_ID: 1" in config
    # 追加分支兜底验证：文件末尾不应出现凭据（XADMIN_APPS 是模板最后一行）
    assert xadmin_idx == len(lines) - 1
    assert redis_idx < xadmin_idx and db_idx < xadmin_idx
    # ALLOWED_HOSTS 原位填在模板注释占位处（Host 校验段，DEBUG 注释段之后、
    # 数据库配置段之前——与模板顺序一致，无重复键）
    assert sum(1 for ln in lines if ln.startswith("ALLOWED_HOSTS:")) == 1
    debug_idx = next(i for i, ln in enumerate(lines) if ln.startswith("# DEBUG: true"))
    db_engine_idx = next(i for i, ln in enumerate(lines) if ln.startswith("DB_ENGINE:"))
    assert debug_idx < allowed_idx < db_engine_idx
    assert _yaml_value(config, "ALLOWED_HOSTS").startswith('["')
    return config, lines


def test_fresh_deploy_fills_placeholders_inplace(tmp_path):
    """模板有注释占位：密码原位解注释填值，不追加到末尾。"""
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
    config_after, lines = _assert_inplace_fill(tmp_path / "config.yml")
    # ALLOWED_HOSTS 已按本机 IP / 回环推导成 JSON 数组（5.0 fail-fast 缺失即拒启）
    allowed_hosts = _yaml_value(config_after, "ALLOWED_HOSTS")
    import json as _json

    parsed = _json.loads(allowed_hosts)
    assert "127.0.0.1" in parsed and "localhost" in parsed
    assert all(isinstance(h, str) for h in parsed)
    # .env 不含 ALLOWED_HOSTS（它是应用配置，不是 compose 凭据）
    assert "ALLOWED_HOSTS=" not in env


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


def test_fallback_appends_when_template_has_no_placeholder(tmp_path):
    """模板连占位都没有：退化为末尾追加，值仍生成且两处一致。"""
    template = "\n".join(
        [
            "# minimal template",
            "DB_ENGINE: postgresql",
            "DB_HOST: postgresql",
            "XADMIN_APPS: [demo]",
            "",
        ]
    )

    result = run_init_config(tmp_path, template=template)
    assert result.returncode == 0, result.stderr

    config = _read(tmp_path / "config.yml")
    lines = config.splitlines()
    assert _yaml_value(config, "DB_PASSWORD") != ""
    assert _yaml_value(config, "REDIS_PASSWORD") != ""
    assert len(_yaml_value(config, "SECRET_KEY")) == 49
    # 追加的内容都在模板最后一行之后；各键间以空行分隔（printf '\n%s: %s\n'），
    # 顺序 SECRET_KEY、DB_PASSWORD、REDIS_PASSWORD、ALLOWED_HOSTS（无占位同样追加）
    tail_lines = [ln for ln in lines if ln.strip()][-4:]
    assert tail_lines[0].startswith("SECRET_KEY:")
    assert tail_lines[1].startswith("DB_PASSWORD:")
    assert tail_lines[2].startswith("REDIS_PASSWORD:")
    assert tail_lines[3].startswith("ALLOWED_HOSTS:")
    # 其他注释行未被误改
    assert "# minimal template" in lines
    env = _read(tmp_path / ".env")
    assert _env_value(env, "DB_PASSWORD") == _yaml_value(config, "DB_PASSWORD")
    assert _env_value(env, "REDIS_PASSWORD") == _yaml_value(config, "REDIS_PASSWORD")
