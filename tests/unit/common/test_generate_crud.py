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
        *extra,
        output=str(backend),
        frontend_root=str(client),
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

    def test_ai_declarations_artifact(self, workspace):
        """默认携带 AI 动作声明骨架（只读动作可直接注册，写动作注释给出）。"""
        backend, _ = _generate(workspace)
        path = backend / "demo" / "ai_declarations.py"
        assert path.exists(), "缺少 AI 声明产物"
        text = path.read_text(encoding="utf8")
        assert "AI_READ_ACTIONS" in text
        assert '"/api/demo/book"' in text
        assert "api_action(" in text
        assert "# AI_WRITE_ACTIONS" in text  # 写动作以注释给出
        assert "TAGGABLE_MODEL_KEYS" not in text  # 未加 --with-tags 时不出现标签声明
        _assert_python_compiles(backend)
        _assert_ruff_clean(backend)

    def test_with_tags_and_tests_flags(self, workspace):
        """--with-tags 附带白名单声明；--with-tests 生成测试骨架。"""
        backend, _ = _generate(workspace, "--with-tags", "--with-tests")
        declarations = (backend / "demo" / "ai_declarations.py").read_text(encoding="utf8")
        assert 'TAGGABLE_MODEL_KEYS = ["demo.book"]' in declarations
        skeleton = backend / "tests" / "unit" / "demo" / "test_book_api.py"
        assert skeleton.exists()
        text = skeleton.read_text(encoding="utf8")
        assert "test_list_requires_auth" in text and 'LIST_URL = "/api/demo/book"' in text
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
        # 同模块 from-import 合并为一行且按字母序（ruff isort 口径，独立成行会 I001）
        assert "from demo.views import BookViewSet, ExistingViewSet" in text
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


class TestBootstrap:
    """--bootstrap：sync_model_field + loaddata 一条龙幂等入库（--grant-to 显式授权）。"""

    def test_bootstrap_loads_seed_and_grants_role(self, workspace):
        from system.models import Menu
        from system.models.role import UserRole

        role = UserRole.objects.create(name="Ops", code="ops")
        backend, _ = _generate(workspace, "--bootstrap", "--grant-to", "ops")

        seed = json.loads((backend / "loadjson" / "seed_demo_book.json").read_text(encoding="utf-8"))
        menu_pks = [entry["pk"] for entry in seed if entry["model"] == "system.menu"]
        assert Menu.objects.filter(pk__in=menu_pks).count() == len(menu_pks)
        # 权限点已关联模型节点（字段权限可用）：bootstrap 内先跑 sync_model_field 并回填种子
        for menu in Menu.objects.filter(pk__in=menu_pks, menu_type=2):
            assert menu.model.count() == 1, f"权限点 {menu.name} 未关联模型节点"
        # 授权：页面菜单 + 全部权限点挂到角色
        assert role.menu.count() == len(menu_pks)

    def test_bootstrap_idempotent(self, workspace):
        """重复 --bootstrap：菜单数与授权数不变（uuid5 确定性 pk upsert + M2M 幂等）。"""
        from system.models import Menu
        from system.models.role import UserRole

        UserRole.objects.create(name="Ops", code="ops")
        # 执行两次本身即断言对象（幂等性来自第二次 run 的 upsert 行为）
        self._run_twice(workspace)
        menu_count = Menu.objects.filter(name__contains="Book").count()
        role_menu_count = UserRole.objects.get(code="ops").menu.count()
        assert menu_count > 0 and role_menu_count > 0

        second_out = self._run_twice(workspace)
        assert Menu.objects.filter(name__contains="Book").count() == menu_count
        assert UserRole.objects.get(code="ops").menu.count() == role_menu_count
        # 二次执行的后续步骤清单不再包含入库步骤（已代办）
        assert "权限点与菜单入库" not in second_out

    @staticmethod
    def _run_twice(workspace) -> str:
        import io

        from django.core.management import call_command

        backend, client = workspace
        out = io.StringIO()
        call_command(
            "generate_crud",
            "demo.Book",
            "--bootstrap",
            "--grant-to",
            "ops",
            "--force",
            output=str(backend),
            frontend_root=str(client),
            stdout=out,
        )
        return out.getvalue()

    def test_bootstrap_without_grant_skips_authorization(self, workspace, capsys):
        from system.models import Menu
        from system.models.role import UserRole

        UserRole.objects.create(name="Ops", code="ops")
        _generate(workspace, "--bootstrap")
        assert Menu.objects.filter(name="DemoBook").exists()
        assert UserRole.objects.get(code="ops").menu.count() == 0
        assert "跳过角色授权" in capsys.readouterr().out


class TestModuleDeclaration:
    """--with-module：顺带生成 {app}/modules.py（模板与 generate_module 同源）。"""

    def test_with_module_writes_declaration(self, workspace):
        backend, _ = _generate(workspace, "--with-module")
        target = backend / "demo" / "modules.py"
        assert target.exists()
        content = target.read_text(encoding="utf-8")
        assert 'id="demo"' in content
        assert 'level="optional"' in content
        assert 'menus=("DemoBook",),' in content
        assert 'routes=("^/api/demo/",),' in content
        compile(content, str(target), "exec")  # 生成物可直接执行
        _assert_ruff_clean(backend)

    def test_module_id_conflict_degrades_to_notice(self, workspace, capsys):
        backend, _ = workspace
        _generate(workspace, "--with-module", "--module-id", "chat")
        assert not (backend / "demo" / "modules.py").exists()
        assert "模块 id 已存在" in capsys.readouterr().out

    def test_module_level_option(self, workspace):
        backend, _ = _generate(workspace, "--with-module", "--module-level", "standard")
        content = (backend / "demo" / "modules.py").read_text(encoding="utf-8")
        assert 'level="standard"' in content

    def test_dry_run_writes_no_module_file(self, workspace):
        backend, client = workspace
        call_command(
            "generate_crud",
            "demo.Book",
            output=str(backend),
            frontend_root=str(client),
            with_module=True,
            dry_run=True,
        )
        assert not (backend / "demo").exists()


class TestNextSteps:
    """生成后的「后续步骤」清单：把手工四件事收敛为可复制命令。"""

    def test_prints_loaddata_doctor_and_authorize(self, workspace, capsys):
        _generate(workspace)
        output = capsys.readouterr().out
        assert "后续步骤" in output
        assert "python manage.py loaddata loadjson/seed_demo_book.json" in output
        assert "python manage.py doctor" in output
        assert "*:DemoBook" in output

    def test_skip_menu_seed_omits_loaddata(self, workspace, capsys):
        _generate(workspace, "--skip-menu-seed")
        output = capsys.readouterr().out
        assert "loaddata" not in output
        assert "后续步骤" in output

    def test_with_module_omits_module_hint(self, workspace, capsys):
        _generate(workspace, "--with-module")
        output = capsys.readouterr().out
        assert "--with-module" not in output.split("后续步骤", 1)[1]


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


class TestImportGrouping:
    """生成物 import 分组：口径与 ruff.toml 的 known-first-party 一致（否则产物 I001）。"""

    def test_unregistered_new_app_treated_as_first_party(self):
        """未登记在 FIRST_PARTY_TOP_LEVEL 的新 app 名必须按第一方归组。

        回归背景（二开走查实测）：新建 app（如 asset）首次 `generate_crud` 时，
        其 import 被当成第三方与 common.* 分组，产物 ruff check 直接 I001——
        即「生成即过门禁」对内置 demo app 成立、对新 app 不成立。
        """
        from common.management.commands._generate_crud.merging import MergeMixin

        lines = [
            "from brandnew.models import Thing",
            "from django_filters import rest_framework as filters",
            "from common.core.filter import BaseFilterSet",
        ]
        assert MergeMixin._group_imports(lines, {"brandnew"}) == [
            "from django_filters import rest_framework as filters",
            "",
            "from brandnew.models import Thing",
            "from common.core.filter import BaseFilterSet",
        ]

    def test_extra_app_keeps_builtin_first_party_intact(self):
        """已登记 app（demo）重复传入 extra 时分组结果不变（幂等）。"""
        from common.management.commands._generate_crud.merging import MergeMixin

        lines = [
            "from demo.models import Book",
            "from common.core.serializers import BaseModelSerializer",
        ]
        assert MergeMixin._group_imports(lines, {"demo"}) == MergeMixin._group_imports(lines)


class TestDefaultOrdering:
    """列表默认排序：模型未声明 Meta.ordering 时由生成物补声明。

    回归背景（二开走查实测）：门禁 tests/unit/system/test_viewset_ordering.py 要求
    列表 ViewSet 声明 ordering 或模型 Meta.ordering 非空——新生成的模块若两边都缺，
    分页会抛 UnorderedObjectListWarning，且新 app 会被门禁直接判失败。
    """

    class _Field:
        def __init__(self, name):
            self.name = name

    def _model(self, field_names, ordering=()):
        field_cls = self._Field

        class _Meta:
            fields = [field_cls(name) for name in field_names]

        _Meta.ordering = ordering

        class _Model:
            _meta = _Meta()

        return _Model

    def test_falls_back_to_pk_without_created_time(self):
        from common.management.commands._generate_crud.analysis import AnalysisMixin

        assert AnalysisMixin._default_ordering(self._model(["id", "name"])) == "-pk"

    def test_uses_created_time_when_present(self):
        from common.management.commands._generate_crud.analysis import AnalysisMixin

        assert AnalysisMixin._default_ordering(self._model(["created_time"])) == "-created_time"

    def test_empty_when_model_declares_ordering(self):
        from common.management.commands._generate_crud.analysis import AnalysisMixin

        assert AnalysisMixin._default_ordering(self._model(["pk"], ordering=("pk",))) == ""

    def test_generated_views_omit_ordering_when_model_declares_it(self, workspace):
        """demo.Book 已有 Meta.ordering → 产物不重复生成 ordering（避免双份真理）。"""
        backend, _ = _generate(workspace)
        views = (backend / "demo" / "views.py").read_text(encoding="utf-8")
        assert "ordering = [" not in views
