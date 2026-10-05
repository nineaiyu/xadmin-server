# -*- coding: utf-8 -*-
"""init_data 引导脚本测试。

超管初始密码不允许硬编码默认值：环境变量 XADMIN_ADMIN_PASSWORD 显式注入优先，
未设置时必须随机生成，杜绝 `xAdminPwd!` 类可猜测凭据随镜像分发。

TestMainFlow 覆盖 main() 编排分支：幂等跳过、call_command 序列、参数开关、
migrate 失败降级不阻断种子导入。外部副作用（call_command / 超管创建）全部
打桩，用例不依赖真实迁移与网络。
"""

import string
import sys

import pytest

from utils.init_data import ADMIN_PASSWORD_ENV, parse_args, resolve_admin_password


class TestResolveAdminPassword:
    def test_env_takes_precedence(self, monkeypatch):
        monkeypatch.setenv(ADMIN_PASSWORD_ENV, "S3cure-Passw0rd!")
        assert resolve_admin_password() == "S3cure-Passw0rd!"

    def test_blank_env_falls_back_to_random(self, monkeypatch):
        monkeypatch.setenv(ADMIN_PASSWORD_ENV, "   ")
        password = resolve_admin_password()
        assert password
        assert password != "   "

    def test_unset_env_generates_random(self, monkeypatch):
        monkeypatch.delenv(ADMIN_PASSWORD_ENV, raising=False)
        password = resolve_admin_password()
        # token_urlsafe(16) 产出的熵足够抵御在线爆破
        assert len(password) >= 20

    def test_random_password_not_repeated(self, monkeypatch):
        monkeypatch.delenv(ADMIN_PASSWORD_ENV, raising=False)
        assert resolve_admin_password() != resolve_admin_password()

    def test_generated_password_matches_urlsafe_alphabet(self, monkeypatch):
        monkeypatch.delenv(ADMIN_PASSWORD_ENV, raising=False)
        password = resolve_admin_password()
        allowed = set(string.ascii_letters + string.digits + "-_")
        assert set(password) <= allowed

    def test_cli_password_takes_precedence(self, monkeypatch):
        monkeypatch.setenv(ADMIN_PASSWORD_ENV, "env-password")
        assert resolve_admin_password("cli-password") == "cli-password"

    def test_cli_blank_falls_through_to_env(self, monkeypatch):
        monkeypatch.setenv(ADMIN_PASSWORD_ENV, "env-password")
        assert resolve_admin_password("   ") == "env-password"


class TestParseArgs:
    """命令行参数（init_data 幂等化改造新增，供 dev_up.sh 编排使用）。"""

    def test_defaults(self):
        args = parse_args([])
        assert args.with_demo is False
        assert args.skip_ip_db is False
        assert args.admin_password == ""

    def test_flags(self):
        args = parse_args(["--with-demo", "--skip-ip-db", "--admin-password", "pwd"])
        assert args.with_demo is True
        assert args.skip_ip_db is True
        assert args.admin_password == "pwd"


class TestMainFlow:
    """main() 编排：外部副作用全部打桩，验证幂等与参数分支。"""

    @pytest.fixture
    def run_main(self, monkeypatch):
        """打桩 call_command 与 UserInfo 管理器，返回 (calls, created, invoke)。"""
        from identity.models import UserInfo
        from utils import init_data

        calls: list[str] = []
        created: dict = {}

        def fake_call_command(name, *args, **kwargs):
            calls.append(name)

        monkeypatch.setattr("django.core.management.call_command", fake_call_command)

        def invoke(argv=(), *, user_exists=False):
            monkeypatch.setattr(sys, "argv", ["init_data.py", *argv])
            monkeypatch.setattr(UserInfo.objects, "exists", lambda: user_exists)
            monkeypatch.setattr(
                UserInfo.objects,
                "create_superuser",
                lambda username, email, password: created.update(username=username, email=email, password=password),
            )
            init_data.main()
            return calls, created

        return invoke

    def test_creates_superuser_with_env_password(self, run_main, monkeypatch):
        monkeypatch.setenv(ADMIN_PASSWORD_ENV, "EnvPass@2026")
        calls, created = run_main()
        assert created == {
            "username": "xadmin",
            "email": "xadmin@dvcloud.xin",
            "password": "EnvPass@2026",
        }
        assert calls == [
            "makemigrations",
            "migrate",
            "compilemessages",
            "download_ip_db",
            "load_init_json",
        ]

    def test_existing_users_skip_superuser_but_seed_still_runs(self, run_main, capsys):
        calls, created = run_main(user_exists=True)
        assert created == {}
        assert "user already exists" in capsys.readouterr().out
        assert calls[-1] == "load_init_json"

    def test_random_password_when_no_explicit_source(self, run_main, monkeypatch, capsys):
        monkeypatch.delenv(ADMIN_PASSWORD_ENV, raising=False)
        calls, created = run_main()
        assert len(created["password"]) >= 20
        out = capsys.readouterr().out
        assert "random password" in out
        assert created["password"] in out  # 随机密码仅打印一次的口径

    def test_explicit_password_not_printed(self, run_main, monkeypatch, capsys):
        monkeypatch.setenv(ADMIN_PASSWORD_ENV, "EnvPass@2026")
        run_main()
        out = capsys.readouterr().out
        assert "EnvPass@2026" not in out
        assert "explicit configuration" in out

    def test_skip_ip_db(self, run_main):
        calls, _ = run_main(argv=["--skip-ip-db"])
        assert "download_ip_db" not in calls
        assert "load_init_json" in calls

    def test_with_demo_runs_seed_after_seed(self, run_main):
        calls, _ = run_main(argv=["--with-demo"])
        assert calls[-2:] == ["load_init_json", "seed_demo_all"]

    def test_migrate_failure_degrades_without_blocking_seed(self, run_main, monkeypatch, capsys):
        import django.core.management

        original = django.core.management.call_command

        def flaky(name, *args, **kwargs):
            if name == "migrate":
                raise RuntimeError("db not ready")
            original(name, *args, **kwargs)

        monkeypatch.setattr("django.core.management.call_command", flaky)
        calls, _ = run_main()
        assert "migrate/compilemessages failed" in capsys.readouterr().out
        assert calls[-1] == "load_init_json"
