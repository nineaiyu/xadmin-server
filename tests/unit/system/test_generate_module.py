# -*- coding: utf-8 -*-
"""`manage.py generate_module` 与「app 侧模块声明」扩展点测试。"""

import importlib.util
import sys
import types
from io import StringIO

import pytest
from django.apps import apps as django_apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError

from common.core import modules
from common.core.modules import ModuleSpec, all_module_specs, module_index


@pytest.fixture
def demo_app(tmp_path, monkeypatch):
    """把已安装的 demo app 指向临时目录，避免脚手架测试写进仓库。"""

    stub = types.SimpleNamespace(name="demo", verbose_name="演示应用", path=str(tmp_path))
    monkeypatch.setitem(django_apps.app_configs, "demo", stub)
    monkeypatch.setattr(django_apps, "get_app_config", lambda label: stub if label == "demo" else None)
    return tmp_path


def run_generate(*args):
    out = StringIO()
    call_command("generate_module", *args, stdout=out)
    return out.getvalue()


class TestGenerateModuleCommand:
    def test_generates_valid_declaration(self, demo_app):
        output = run_generate(
            "my_biz", "--app", "demo", "--label", "自有业务", "--menu", "Chat", "--route", "^/api/demo/"
        )
        target = demo_app / "modules.py"
        assert target.exists()
        content = target.read_text(encoding="utf-8")
        compile(content, str(target), "exec")  # 生成物必须是可执行 Python
        assert 'id="my_biz"' in content
        assert 'label="自有业务"' in content
        assert 'level="optional"' in content
        # 双引号元组字面量：生成物需一次通过 ruff format（quote-style=double）
        assert 'menus=("Chat",)' in content
        assert 'routes=("^/api/demo/",)' in content
        assert "management.py modules" not in output  # 提示文案不含拼写错误
        assert "python manage.py modules" in output

    def test_multi_menu_and_permission_options(self, demo_app):
        run_generate(
            "my_biz",
            "--app",
            "demo",
            "--menu",
            "Chat",
            "--menu",
            "AiAssistant",
            "--permission",
            "api/system/ai/",
        )
        content = (demo_app / "modules.py").read_text(encoding="utf-8")
        assert 'menus=("Chat", "AiAssistant"),' in content
        assert 'permissions=("api/system/ai/",),' in content

    def test_unknown_menu_warns(self, demo_app):
        output = run_generate("my_biz", "--app", "demo", "--menu", "NotExistMenu")
        assert "不在内置模块清单中" in output

    def test_duplicate_module_id_rejected(self, demo_app):
        with pytest.raises(CommandError, match="模块 id 已存在"):
            run_generate("chat", "--app", "demo")

    def test_unknown_app_rejected(self, demo_app):
        with pytest.raises(CommandError, match="未安装的 app"):
            run_generate("my_biz", "--app", "not_installed")

    def test_existing_file_requires_force(self, demo_app):
        run_generate("my_biz", "--app", "demo")
        with pytest.raises(CommandError, match="已存在"):
            run_generate("my_biz", "--app", "demo")
        run_generate("my_biz", "--app", "demo", "--force")  # 覆盖成功


class TestAppModuleDiscovery:
    """`{app}/modules.py` 声明随 app 安装自动纳入清单（第三方模块扩展点）。"""

    def _register_declaration(self, monkeypatch, specs, module_name="demo.modules"):
        fake = types.ModuleType(module_name)
        fake.MODULES = specs
        monkeypatch.setitem(sys.modules, module_name, fake)

        class _AppConfig:
            name = "demo"

        monkeypatch.setattr(django_apps, "get_app_configs", lambda: [_AppConfig()])

    def test_declared_module_is_discovered(self, monkeypatch, module_config):
        spec = ModuleSpec("my_biz", "自有业务", "optional", routes=(r"^/api/demo/",))
        self._register_declaration(monkeypatch, (spec,))
        module_config()  # 清缓存并保证测试后复位

        assert "my_biz" in {item.id for item in all_module_specs()}
        assert "my_biz" in module_index()
        # 声明为 optional：默认 full 预设下自动启用
        assert modules.is_module_enabled("my_biz") is True

    def test_duplicate_id_rejected(self, monkeypatch, module_config):
        self._register_declaration(monkeypatch, (ModuleSpec("chat", "冲突模块"),))
        module_config()
        with pytest.raises(ImproperlyConfigured, match="模块 id 重复"):
            module_index()

    def test_broken_declaration_is_skipped(self, monkeypatch, module_config):
        self._register_declaration(monkeypatch, 42)  # 非可迭代 → 告警跳过，不影响内核
        module_config()
        assert "core_rbac" in module_index()
        assert "my_biz" not in module_index()

    def test_missing_declaration_is_ignored(self, module_config):
        module_config()
        assert modules.discovered_modules() == ()

    def test_generated_file_is_importable_and_discoverable(self, demo_app, monkeypatch, module_config):
        """生成 → 真实加载 → 发现机制纳入清单：脚手架产物可直接被扩展点消费。"""

        run_generate("my_biz", "--app", "demo", "--label", "自有业务", "--menu", "Chat", "--route", "^/api/demo/")

        target = demo_app / "modules.py"
        spec = importlib.util.spec_from_file_location("demo.modules", target)
        generated = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generated)  # 生成物可直接执行（此前仅 compile 校验）
        assert [item.id for item in generated.MODULES] == ["my_biz"]
        assert generated.MODULES[0].menus == ("Chat",)
        assert generated.MODULES[0].routes == ("^/api/demo/",)

        class _AppConfig:
            name = "demo"

        monkeypatch.setattr(django_apps, "get_app_configs", lambda: [_AppConfig()])
        monkeypatch.setitem(sys.modules, "demo.modules", generated)
        module_config()  # 清缓存触发重新发现
        assert "my_biz" in {item.id for item in modules.discovered_modules()}
        assert "my_biz" in module_index()
        assert modules.is_module_enabled("my_biz") is True  # optional 随 full 预设默认开启
