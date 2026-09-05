# -*- coding: utf-8 -*-
"""init_data 引导脚本测试（TD-22）。

超管初始密码不允许硬编码默认值：环境变量 XADMIN_ADMIN_PASSWORD 显式注入优先，
未设置时必须随机生成，杜绝 `xAdminPwd!` 类可猜测凭据随镜像分发。
"""

import string

from utils.init_data import ADMIN_PASSWORD_ENV, resolve_admin_password


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
