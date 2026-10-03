# -*- coding: utf-8 -*-
"""启动流程：数据库迁移的显式开关（多副本/滚动发布前置）。

- ``AUTO_MIGRATE`` 默认开：单副本形态行为不变（web 容器启动即迁移）；
- 置 false：跳过启动迁移，由一次性 migrate 服务先跑完（entrypoint 的 migrate 动作）。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

_STUBBED_STEPS = (
    "check_database_connection",
    "collect_static",
    "compile_i18n_file",
    "check_port_is_used",
    "expire_caches",
    "download_ip_db",
    "check_permission_gaps",
)


def _hands():
    from common.management.commands.services import hands

    return hands


def _stub_steps(monkeypatch, calls):
    hands = _hands()
    for name in _STUBBED_STEPS:
        monkeypatch.setattr(hands, name, lambda *args, _name=name, **kwargs: calls.append(_name))
    monkeypatch.setattr(hands, "perform_db_migrate", lambda: calls.append("perform_db_migrate"))


class TestAutoMigrateSwitch:
    def test_default_runs_migrate(self, monkeypatch):
        calls = []
        _stub_steps(monkeypatch, calls)
        hands = _hands()
        monkeypatch.setattr(hands, "AUTO_MIGRATE", True)
        hands.server_prepare()
        assert "perform_db_migrate" in calls

    def test_disabled_skips_migrate_only(self, monkeypatch):
        """关闭只跳过迁移，其余启动自检/缓存失效照常执行。"""
        calls = []
        _stub_steps(monkeypatch, calls)
        hands = _hands()
        monkeypatch.setattr(hands, "AUTO_MIGRATE", False)
        hands.server_prepare()
        assert "perform_db_migrate" not in calls
        assert "check_database_connection" in calls
        assert "expire_caches" in calls
        assert "check_permission_gaps" in calls

    def test_config_key_registered(self):
        """配置键须登记在 conf 默认值表（否则 CONFIG.AUTO_MIGRATE 静默为 None）。"""
        from server.conf import Config

        assert Config.defaults["AUTO_MIGRATE"] is True


class TestEntrypointMigrateAction:
    def test_migrate_action_precedes_pid_cleanup(self):
        """migrate 动作必须**先于** pid 清理返回。

        pid 清理的 ``service`` 缺省是 all，会删掉其它运行中容器管理的 pid 文件
        （entrypoint 注释已说明该风险）；一次性迁移容器走该分支会误伤常驻服务。
        """
        text = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
        migrate_at = text.index('"$action" == "migrate"')
        cleanup_at = text.index("rm -f /data/xadmin-server/tmp/*.pid")
        assert migrate_at < cleanup_at
        assert "manage.py migrate" in text[migrate_at : migrate_at + 200]

    def test_prod_overlay_defines_one_off_migrate_service(self):
        """prod overlay 提供一次性 migrate 服务（无 container_name，不常驻）。"""
        text = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
        assert "\n  migrate:" in text
        assert 'command: ["migrate"]' in text
        assert 'restart: "no"' in text
        assert "container_name" not in text.split("\n  migrate:")[1].split("\n  server:")[0]
