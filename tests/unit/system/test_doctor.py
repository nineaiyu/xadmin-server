# -*- coding: utf-8 -*-
"""doctor / post_upgrade 管理命令守护测试。

doctor 是「只读自检 + 修复命令提示」，本文件钉死：
1. 在可用的测试环境（sqlite + FakeRedis）下能完整跑通、无失败项（不抛 SystemExit）；
2. 关键检查项出现在输出（配置与密钥 / 数据库 / Redis / 语言包 / 模块裁剪 / 契约镜像）；
3. --skip-permissions 生效；
4. post_upgrade 的参数分支可跑通且输出完成标记。

注意：doctor 的失败项（❌）会触发 SystemExit(1)——测试环境 DB/Redis 均可用，
不应出现失败项；此处用「不抛异常」作为跨环境稳定的断言口径。
"""

import json
import sys
from io import StringIO

import pytest
from django.conf import settings
from django.core.management import call_command

# doctor 会连数据库（连通性检查 / 权限点扫描），必须允许 DB 访问
pytestmark = pytest.mark.django_db


def _run(cmd, *args, **kwargs):
    out = StringIO()
    call_command(cmd, *args, stdout=out, **kwargs)
    return out.getvalue()


class TestDoctor:
    def test_full_run_outputs_all_sections(self):
        output = _run("doctor")
        for section in (
            "环境自检",
            "配置与密钥",
            "数据库",
            "Redis",
            "语言包",
            "模块裁剪",
            "契约镜像",
            "版本一致",
            "结果：",
        ):
            assert section in output, f"缺少检查项输出：{section}"

    def test_skip_permissions_section_absent(self):
        output = _run("doctor", "--skip-permissions")
        assert "权限点：" not in output
        assert "结果：" in output

    def test_no_failure_exit_in_test_env(self):
        # 测试环境 DB（sqlite）与缓存（FakeRedis）均可用：不应有任何 ❌ 导致退出
        output = _run("doctor")
        assert "失败 0" in output


class TestLocaleCheck:
    """语言包检查：缺失 .mo 时报出**语言码**（zh/en），而不是父目录名 locale。"""

    def _prepare(self, tmp_path, *, stale_lang):
        base = tmp_path / "server"
        for lang in ("zh", "en"):
            target = base / "locale" / lang / "LC_MESSAGES"
            target.mkdir(parents=True)
            (target / "django.po").write_text("", encoding="utf-8")
            if lang != stale_lang:
                (target / "django.mo").write_text("", encoding="utf-8")
        return base

    def test_reports_language_code(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "BASE_DIR", str(self._prepare(tmp_path, stale_lang="zh")))
        output = _run("doctor")
        assert "缺失或未更新：zh" in output
        assert "缺失或未更新：locale" not in output

    def test_compiled_locale_passes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "BASE_DIR", str(self._prepare(tmp_path, stale_lang="")))
        output = _run("doctor")
        assert "2 个语言包已编译" in output


class TestModuleCheck:
    """模块裁剪检查：区分「显式增删（合法配置）」与「后台覆盖行（盖住基线，需清理）」。"""

    class _Resolution:
        preset = "full"
        enabled = frozenset({"a"})
        disabled = frozenset({"b"})
        overrides = ("chat-",)

    def _patch(self, monkeypatch, *, override_row: bool):
        from common.core import modules as mod

        monkeypatch.setattr(mod, "validate_deployment_config", lambda: self._Resolution())
        monkeypatch.setattr(mod, "modules_report", lambda resolution=None: [{"enabled": True}, {"enabled": False}])
        monkeypatch.setattr(mod, "override_active", lambda: override_row)

    def test_explicit_entries_are_pass_without_clear_fix(self, monkeypatch):
        self._patch(monkeypatch, override_row=False)
        output = _run("doctor")
        assert "显式增删 1 条（chat-）" in output
        assert "后台覆盖行" not in output
        assert "--clear-override" not in output

    def test_db_override_row_warns_with_clear_fix(self, monkeypatch):
        self._patch(monkeypatch, override_row=True)
        output = _run("doctor")
        assert "后台覆盖行" in output
        assert "--clear-override" in output
        assert "失败 0" in output  # 覆盖行只是告警，不判失败

    def test_invalid_config_reports_fail_with_fix(self, monkeypatch):
        """非法模块名必须由 doctor 报出（FAIL + 修复命令），而不是 setup 阶段的裸 traceback。"""
        from django.core.exceptions import ImproperlyConfigured

        from common.core import modules as mod

        def _boom():
            raise ImproperlyConfigured("MODULE_ENABLE/MODULE_DISABLE 中存在未知模块：not_exist")

        monkeypatch.setattr(mod, "validate_deployment_config", _boom)
        out = StringIO()
        with pytest.raises(SystemExit):
            call_command("doctor", stdout=out)
        output = out.getvalue()
        assert "配置非法" in output
        assert "not_exist" in output
        assert "MODULE_PRESET / MODULE_ENABLE / MODULE_DISABLE" in output


class TestReadyExcludesDoctor:
    """doctor 是诊断入口：模块配置非法时也必须能跑起来（ready() 不得先行 fail-fast）。"""

    def test_ready_skips_module_validation_for_doctor(self, monkeypatch):
        from django.apps import apps as django_apps

        from common.core import modules as mod

        calls = []
        monkeypatch.setattr(mod, "validate_deployment_config", lambda: calls.append(1))
        monkeypatch.setattr(sys, "argv", ["manage.py", "doctor"])

        django_apps.get_app_config("common").ready()

        assert calls == [], "doctor 命令下 ready() 不应做模块配置校验（否则 doctor 无法诊断非法配置）"


class TestVersionSyncCheck:
    """前后端版本一致性（doctor 第 8 项）：同工作区检出前端时比对，缺失跳过、不一致告警。"""

    @staticmethod
    def _prepare(tmp_path, client_version):
        """构造隔离的 BASE_DIR 与同级 xadmin-client/package.json。"""
        base = tmp_path / "xadmin-server"
        base.mkdir()
        client = tmp_path / "xadmin-client"
        client.mkdir()
        (client / "package.json").write_text(json.dumps({"version": client_version}), encoding="utf-8")
        return base

    def test_skips_without_client_repo(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "BASE_DIR", str(tmp_path / "xadmin-server-none"))
        output = _run("doctor")
        assert "未检出前端仓库" in output

    def test_match_passes(self, monkeypatch, tmp_path):
        from server.const import VERSION

        monkeypatch.setattr(settings, "BASE_DIR", str(self._prepare(tmp_path, VERSION)))
        output = _run("doctor")
        assert f"前后端版本一致（{VERSION}）" in output

    def test_mismatch_warns_with_fix(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "BASE_DIR", str(self._prepare(tmp_path, "0.0.1")))
        output = _run("doctor")
        assert "0.0.1" in output
        assert "同步修改 server/const.py" in output
        assert "失败 0" in output  # 版本不一致只告警，不判失败

    def test_broken_package_json_warns(self, monkeypatch, tmp_path):
        base = self._prepare(tmp_path, "0.0.1")
        (tmp_path / "xadmin-client" / "package.json").write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(settings, "BASE_DIR", str(base))
        output = _run("doctor")
        assert "解析失败" in output


class TestPostUpgrade:
    def test_skip_branches_run_through(self):
        output = _run("post_upgrade", "--skip-seed", "--skip-compile", "--skip-permissions")
        assert "跳过种子导入" in output
        assert "跳过语言包编译" in output
        assert "跳过权限点扫描" in output
        assert "post_upgrade 完成" in output

    def test_permission_scan_branch(self):
        output = _run("post_upgrade", "--skip-seed", "--skip-compile")
        assert "权限点缺口扫描" in output
        assert "post_upgrade 完成" in output


class _FakeRoute:
    url = "api/demo/book$"


class TestStartupPermissionCheck:
    """启动自检（hands.check_permission_gaps）：仅 DEV 告警、不阻塞启动（P1-2）。"""

    def _hands(self, monkeypatch, debug: bool):
        from common.management.commands.services import hands

        monkeypatch.setattr(hands, "DEBUG", debug)
        return hands

    def test_disabled_when_debug_off(self, monkeypatch):
        from system.utils import permission_sync

        hands = self._hands(monkeypatch, debug=False)
        called = []
        monkeypatch.setattr(permission_sync, "scan_permission_gaps", lambda: called.append(1) or [])

        hands.check_permission_gaps()

        assert called == [], "生产（DEBUG=false）不应执行启动期权限点扫描"

    def test_warns_when_gaps_found(self, monkeypatch):
        from system.utils import permission_sync

        hands = self._hands(monkeypatch, debug=True)
        warnings = []
        monkeypatch.setattr(permission_sync, "scan_permission_gaps", lambda: [(_FakeRoute(), "POST", "create")])
        monkeypatch.setattr(hands.logger, "warning", lambda msg: warnings.append(msg))

        hands.check_permission_gaps()

        assert warnings and "权限点缺口" in warnings[0]

    def test_swallows_scan_errors(self, monkeypatch):
        from system.utils import permission_sync

        hands = self._hands(monkeypatch, debug=True)
        warnings = []

        def _boom():
            raise RuntimeError("boom")

        monkeypatch.setattr(permission_sync, "scan_permission_gaps", _boom)
        monkeypatch.setattr(hands.logger, "warning", lambda msg: warnings.append(msg))

        hands.check_permission_gaps()  # 不应抛出（自检失败不得阻塞启动）

        assert warnings and "跳过" in warnings[0]
