# -*- coding: utf-8 -*-
"""代码生成器单测。

覆盖：产物清单与关键结构、生成即过门禁（ruff check + ruff format --check）、
幂等（重复执行不改内容 / 生成块不重复）、共享文件合并（import 去重、urls 注册行插入）、
菜单种子结构（权限码 / 路径正则 / uuid5 确定性）、--dry-run 不落盘。

样本用 `demo.Book`（demo 仅在测试 settings 启用，见 tests/settings_test.py）。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command

from common.management.commands.generate_crud import (
    BLOCK_END,
    BLOCK_START,
    PERMISSION_ACTIONS,
)

pytestmark = pytest.mark.django_db


def _ruff():
    candidates = [
        shutil.which("ruff"),
        str(Path(settings.PROJECT_DIR) / ".venv" / "bin" / "ruff"),
        str(Path(settings.PROJECT_DIR) / ".venv" / "Scripts" / "ruff.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    pytest.fail("未找到 ruff：生成物必须过 ruff check / ruff format 门禁（pip install -r requirements-dev.txt）")


def _assert_ruff_clean(target: Path):
    config = str(Path(settings.PROJECT_DIR) / "ruff.toml")
    for args in (["check", "--no-cache"], ["format", "--check"]):
        result = subprocess.run(
            [_ruff(), *args, "--config", config, str(target)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"ruff {' '.join(args)} 未通过：\n{result.stdout}\n{result.stderr}"


def _assert_python_compiles(target: Path):
    for path in target.rglob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


@pytest.fixture
def workspace(tmp_path):
    """隔离的输出根 + 前端根。"""
    backend = tmp_path / "server"
    client = tmp_path / "client"
    backend.mkdir()
    client.mkdir()
    return backend, client


def _generate(workspace, *extra):
    backend, client = workspace
    call_command(
        "generate_crud",
        "demo.Book",
        output=str(backend),
        frontend_root=str(client),
        *extra,
    )
    return backend, client


class TestArtifacts:
    def test_backend_and_frontend_artifacts(self, workspace):
        backend, client = _generate(workspace)
        expected = [
            backend / "demo" / "serializers.py",
            backend / "demo" / "views.py",
            backend / "demo" / "urls.py",
            backend / "demo" / "config.py",
            backend / "loadjson" / "seed_demo_book.json",
            client / "src" / "views" / "demo" / "book" / "index.vue",
            client / "src" / "views" / "demo" / "book" / "utils" / "api.ts",
            client / "src" / "views" / "demo" / "book" / "utils" / "hook.tsx",
        ]
        for path in expected:
            assert path.exists(), f"缺少产物：{path}"
        _assert_python_compiles(backend)
        _assert_ruff_clean(backend)

    def test_ruff_clean_when_appending_to_existing_views(self, workspace):
        """共享文件合并路径同样要过门禁（E402 已全局忽略，import 去重防 F811）。"""
        backend, _ = workspace
        app_dir = backend / "demo"
        app_dir.mkdir(parents=True)
        (app_dir / "views.py").write_text(
            "#!/usr/bin/env python\n"
            "# -*- coding:utf-8 -*-\n"
            "from common.core.modelset import BaseModelSet\n"
            "\n"
            "\n"
            "class ExistingViewSet(BaseModelSet):\n"
            '    """已存在的视图"""\n',
            encoding="utf-8",
        )
        _generate(workspace)
        text = (app_dir / "views.py").read_text(encoding="utf-8")
        # 已导入的 BaseModelSet 不再重复导入（F811 守护）
        assert text.count("from common.core.modelset import BaseModelSet") == 1
        assert "class BookViewSet(BaseModelSet):" in text
        _assert_ruff_clean(backend)

    def test_serializer_excludes_audit_fields(self, workspace):
        backend, _ = _generate(workspace)
        serializer = (backend / "demo" / "serializers.py").read_text(encoding="utf-8")
        # 审计字段（creator/modifier/dept_belong）由框架维护，不入序列化器可写字段
        assert '"creator"' not in serializer
        assert '"modifier"' not in serializer
        assert '"dept_belong"' not in serializer
        assert "        model = models.Book" in serializer

    def test_views_and_urls_structure(self, workspace):
        backend, _ = _generate(workspace)
        views = (backend / "demo" / "views.py").read_text(encoding="utf-8")
        assert "class BookViewSetFilter(BaseFilterSet):" in views
        assert 'name = filters.CharFilter(field_name="name", lookup_expr="icontains")' in views
        assert '    ordering_fields = ["created_time"]' in views
        urls = (backend / "demo" / "urls.py").read_text(encoding="utf-8")
        assert 'router.register("book", BookViewSet, basename="book")' in urls
        assert "router = SimpleRouter(False)" in urls

    def test_dry_run_writes_nothing(self, workspace, capsys):
        backend, client = workspace
        call_command(
            "generate_crud",
            "demo.Book",
            output=str(backend),
            frontend_root=str(client),
            dry_run=True,
        )
        output = capsys.readouterr().out
        assert "dry-run" in output
        assert not (backend / "demo").exists()
        assert not (client / "src").exists()


class TestIdempotency:
    def test_second_run_skips_and_keeps_content(self, workspace):
        backend, client = _generate(workspace)
        serializers = (backend / "demo" / "serializers.py").read_text(encoding="utf-8")
        views = (backend / "demo" / "views.py").read_text(encoding="utf-8")
        _generate(workspace)
        assert (backend / "demo" / "serializers.py").read_text(encoding="utf-8") == serializers
        assert (backend / "demo" / "views.py").read_text(encoding="utf-8") == views
        # 生成块只出现一次（重复执行不会叠加）
        key = "views-book"
        assert views.count(BLOCK_START.format(key=key)) == 1
        assert views.count("class BookViewSet(BaseModelSet):") == 1

    def test_force_replaces_block_without_duplication(self, workspace):
        backend, _ = _generate(workspace)
        views_path = backend / "demo" / "views.py"
        _generate(workspace, "--force")
        text = views_path.read_text(encoding="utf-8")
        key = "views-book"
        assert text.count(BLOCK_START.format(key=key)) == 1
        assert text.count(BLOCK_END.format(key=key)) == 1
        assert text.count("class BookViewSet(BaseModelSet):") == 1
        _assert_ruff_clean(backend)

    def test_urls_registration_is_idempotent(self, workspace):
        backend, _ = _generate(workspace)
        urls_path = backend / "demo" / "urls.py"
        before = urls_path.read_text(encoding="utf-8")
        _generate(workspace, "--force")
        after = urls_path.read_text(encoding="utf-8")
        assert after.count('router.register("book", BookViewSet, basename="book")') == 1
        assert before == after


class TestUrlsMerge:
    def test_register_line_inserted_before_urlpatterns(self, workspace):
        backend, _ = workspace
        app_dir = backend / "demo"
        app_dir.mkdir(parents=True)
        (app_dir / "urls.py").write_text(
            "#!/usr/bin/env python\n"
            "# -*- coding:utf-8 -*-\n"
            "from rest_framework.routers import SimpleRouter\n"
            "\n"
            "from demo.views import ExistingViewSet\n"
            "\n"
            'app_name = "demo"\n'
            "\n"
            "router = SimpleRouter(False)\n"
            "\n"
            'router.register("existing", ExistingViewSet, basename="existing")\n'
            "\n"
            "urlpatterns = []\n"
            "urlpatterns += router.urls\n",
            encoding="utf-8",
        )
        _generate(workspace)
        text = (app_dir / "urls.py").read_text(encoding="utf-8")
        assert "from demo.views import BookViewSet" in text
        assert text.index('router.register("book"') < text.index("urlpatterns = []")
        assert 'router.register("existing"' in text
        _assert_python_compiles(backend)
        _assert_ruff_clean(backend)


class TestMenuSeed:
    def test_seed_structure_and_deterministic_pk(self, workspace):
        backend, _ = _generate(workspace)
        seed_path = backend / "loadjson" / "seed_demo_book.json"
        entries = json.loads(seed_path.read_text(encoding="utf-8"))
        permissions = [item for item in entries if item["model"] == "system.menu" and item["fields"]["menu_type"] == 2]
        assert [item["fields"]["name"] for item in permissions] == [
            f"{action}:DemoBook" for action, _method, _path in PERMISSION_ACTIONS
        ]
        list_permission = permissions[0]["fields"]
        assert list_permission["method"] == "GET"
        assert list_permission["path"] == "api/demo/book$"
        detail = permissions[1]["fields"]
        assert detail["path"] == "api/demo/book/(?P<pk>[^/.]+)$"
        assert detail["method"] == "GET"
        # 权限码挂到页面菜单下，页面菜单是 MENU 类型
        page_menu = next(
            item for item in entries if item["model"] == "system.menu" and item["fields"]["menu_type"] == 1
        )
        assert page_menu["fields"]["component"] == "demo/book/index"
        assert {item["fields"]["parent"] for item in permissions} == {page_menu["pk"]}
        # 重复生成：同一批 pk（uuid5 确定性，重复 loaddata 覆盖同一批行）
        before = seed_path.read_text(encoding="utf-8")
        _generate(workspace, "--force")
        assert seed_path.read_text(encoding="utf-8") == before

    def test_seed_importable_by_loaddata(self, workspace):
        """种子必须是 Django fixture 可加载形态（这里校验必需字段已齐备）。"""
        backend, _ = _generate(workspace)
        entries = json.loads((backend / "loadjson" / "seed_demo_book.json").read_text(encoding="utf-8"))
        menus = [item for item in entries if item["model"] == "system.menu"]
        for menu in menus:
            for required in ("name", "path", "menu_type", "meta", "is_active"):
                assert required in menu["fields"], f"{menu['fields']['name']} 缺少 {required}"
        # 模型未同步（ModelLabelField 不存在）时留空数组，不生成悬空引用
        assert all(menu["fields"]["model"] == [] for menu in menus)


class TestPackageMode:
    def test_writes_module_files_into_packages(self, workspace):
        """serializers / views 为包时各写独立模块，urls 的 import 指向具体模块。"""
        backend, _ = _generate(workspace)
        assert (backend / "demo" / "serializers.py").exists()  # 无包时走共享文件合并
        app_dir = backend / "demo"
        for name in ("serializers", "views"):
            (app_dir / name).mkdir()
            (app_dir / name / "__init__.py").write_text("", encoding="utf-8")
        (app_dir / "urls.py").unlink()
        _generate(workspace, "--force")
        views = app_dir / "views" / "book.py"
        assert views.exists()
        assert "from demo.serializers.book import BookSerializer" in views.read_text(encoding="utf-8")
        urls = (app_dir / "urls.py").read_text(encoding="utf-8")
        assert "from demo.views.book import BookViewSet" in urls
        _assert_python_compiles(backend)
        _assert_ruff_clean(backend)


class TestOptions:
    def test_menu_parent_option(self, workspace):
        parent = "11111111-1111-1111-1111-111111111111"
        backend, client = workspace
        call_command(
            "generate_crud",
            "demo.Book",
            output=str(backend),
            frontend_root=str(client),
            parent=parent,
        )
        entries = json.loads((backend / "loadjson" / "seed_demo_book.json").read_text(encoding="utf-8"))
        page_menu = next(item for item in entries if item["fields"].get("menu_type") == 1)
        assert page_menu["fields"]["parent"] == parent

    def test_with_import_export_adds_mixins_and_permissions(self, workspace):
        backend, _ = _generate(workspace, "--with-import-export")
        views = (backend / "demo" / "views.py").read_text(encoding="utf-8")
        assert "class BookViewSet(BaseModelSet, ImportExportDataAction):" in views
        entries = json.loads((backend / "loadjson" / "seed_demo_book.json").read_text(encoding="utf-8"))
        names = [item["fields"]["name"] for item in entries if item["fields"].get("menu_type") == 2]
        assert "exportData:DemoBook" in names
        assert "importData:DemoBook" in names
        _assert_ruff_clean(backend)

    def test_skip_frontend_and_menu_seed(self, workspace):
        backend, client = _generate(workspace, "--skip-frontend", "--skip-menu-seed")
        assert (backend / "demo" / "urls.py").exists()
        assert not (client / "src").exists()
        assert not (backend / "loadjson").exists()

    def test_unknown_model_rejected(self, workspace):
        from django.core.management.base import CommandError

        backend, client = workspace
        with pytest.raises(CommandError):
            call_command(
                "generate_crud",
                "demo.NotExist",
                output=str(backend),
                frontend_root=str(client),
            )
