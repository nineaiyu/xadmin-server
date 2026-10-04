# -*- coding:utf-8 -*-
"""代码生成器 GUI 端点守护测试（适配层 system/utils/platform/codegen_gui.py）。

覆盖：模型清单过滤（仓库内 / 非抽象）、字段计划回显、字段选择收敛
（include/exclude 与未知字段拒绝）、产物预览（不落盘）、zip 下载。
"""

import io
import zipfile

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from system.utils.platform import codegen_gui
from system.views.admin.codegen import SystemCodeGenViewSet

pytestmark = pytest.mark.django_db

pytest.importorskip("demo", reason="demo app 未启用时跳过（模块裁剪环境）")


def _view(action):
    return SystemCodeGenViewSet.as_view({"get": action, "post": action})


def _get(user, action, params=None):
    factory = APIRequestFactory()
    request = factory.get(f"/api/system/codegen/{action}", params or {})
    force_authenticate(request, user=user)
    return _view(action)(request)


def _post(user, action, payload):
    factory = APIRequestFactory()
    request = factory.post(f"/api/system/codegen/{action}", payload, format="json")
    force_authenticate(request, user=user)
    return _view(action)(request)


class TestModels:
    def test_lists_repo_models_with_demo_book(self, superuser):
        response = _get(superuser, "models")
        assert response.status_code == 200
        labels = [row["label"] for row in response.data["data"]]
        assert "demo.Book" in labels
        # django.contrib 不在生成面
        assert not any(label.startswith("auth.") for label in labels)

    def test_model_fields_returns_plan_and_defaults(self, superuser):
        response = _get(superuser, "model_fields", {"label": "demo.Book"})
        assert response.status_code == 200
        data = response.data["data"]
        assert data["label"] == "demo.Book"
        assert data["defaults"]["component"] == "DemoBook"
        names = [field["name"] for field in data["fields"]]
        assert "pk" in names and "name" in names

    def test_model_fields_unknown_model_rejected(self, superuser):
        response = _get(superuser, "model_fields", {"label": "nope.Missing"})
        assert response.data["code"] == 1001


class TestPreview:
    def test_preview_artifacts_without_disk_write(self, superuser, tmp_path, monkeypatch):
        # 落盘探针：预览路径不得触发任何文件写入（仅允许读取既有文件做合并语义）
        watched = tmp_path / "canary.txt"
        watched.write_text("untouched", encoding="utf-8")
        monkeypatch.setattr(
            "pathlib.Path.write_text",
            lambda self, *a, **k: (_ for _ in ()).throw(AssertionError("preview must not write")),
        )
        response = _post(superuser, "preview", {"model": "demo.Book"})
        assert response.status_code == 200
        artifacts = response.data["data"]
        labels = [row["label"] for row in artifacts]
        assert "序列化器" in labels and "视图" in labels and "菜单种子" in labels
        assert "后续步骤" in labels  # zip 附带 NEXT_STEPS.md，预览同步可见
        paths = [row["path"] for row in artifacts]
        assert any(path.startswith("xadmin-server/demo/") for path in paths)
        assert any(path.startswith("xadmin-client/src/views/demo/") for path in paths)
        assert any(path == "NEXT_STEPS.md" for path in paths)
        assert watched.read_text(encoding="utf-8") == "untouched"

    def test_exclude_fields_shrinks_serializer_and_table(self, superuser):
        base = _post(superuser, "preview", {"model": "demo.Book"}).data["data"]
        base_serializer = next(row for row in base if row["label"] == "序列化器")
        assert '"name"' in base_serializer["content"]

        trimmed = _post(superuser, "preview", {"model": "demo.Book", "exclude_fields": ["name"]}).data["data"]
        trimmed_serializer = next(row for row in trimmed if row["label"] == "序列化器")
        assert '"name"' not in trimmed_serializer["content"]
        assert '"price"' in trimmed_serializer["content"]

    def test_include_fields_whitelists(self, superuser):
        response = _post(superuser, "preview", {"model": "demo.Book", "include_fields": ["name"]}).data["data"]
        serializer = next(row for row in response if row["label"] == "序列化器")
        assert '"name"' in serializer["content"]
        # 未选中的字段不出现在序列化器 fields 面
        assert '"isbn"' not in serializer["content"]

    def test_unknown_field_rejected(self, superuser):
        response = _post(superuser, "preview", {"model": "demo.Book", "exclude_fields": ["not_a_field"]})
        assert response.data["code"] == 1001
        assert "not_a_field" in response.data["detail"]

    def test_pk_cannot_be_excluded(self, superuser):
        response = _post(superuser, "preview", {"model": "demo.Book", "exclude_fields": ["pk"]}).data["data"]
        serializer = next(row for row in response if row["label"] == "序列化器")
        assert '"pk"' in serializer["content"]


def _serializer(artifacts):
    return next(row for row in artifacts if row["label"] == "序列化器")["content"]


class TestModelPlan:
    def test_plan_has_field_flags_and_dict_types(self, superuser):
        data = _get(superuser, "model_fields", {"label": "demo.Book"}).data["data"]
        fields = {field["name"]: field for field in data["fields"]}
        # 关联字段带默认 input_type（admin 为 User 外键 → api-search-user）
        assert fields["admin"]["is_relation"] is True
        assert fields["admin"]["default_input_type"] == "api-search-user"
        # 文本字段可 icontains 自定义过滤；choices 字段有标记
        assert fields["name"]["can_filter_custom"] is True
        assert fields["category"]["has_choices"] is True
        # 可绑定字典类型清单随计划返回（数据库可用时不为空也不断言内容）
        assert isinstance(data["dict_types"], list)


class TestFieldOverrides:
    """字段级自定义：label / required / read_only / input_type / 字典 / 开关 / 排序。"""

    def test_label_and_required_written_to_extra_kwargs(self, superuser):
        payload = {
            "model": "demo.Book",
            "fields": [{"name": "name", "label": "书名", "required": True}],
        }
        serializer = _serializer(_post(superuser, "preview", payload).data["data"])
        assert '"name": {' in serializer
        assert '"label": "书名"' in serializer
        assert '"required": True' in serializer

    def test_read_only_clears_required(self, superuser):
        payload = {
            "model": "demo.Book",
            "fields": [{"name": "name", "read_only": True, "required": True}],
        }
        serializer = _serializer(_post(superuser, "preview", payload).data["data"])
        block = serializer.split('"name": {', 1)[1].split("},", 1)[0]
        assert '"read_only": True' in block
        assert '"required"' not in block

    def test_include_false_excludes_and_reorders(self, superuser):
        payload = {
            "model": "demo.Book",
            # isbn 在引擎序中位于 name 之后：显式调换顺序并排除 author
            "fields": [
                {"name": "isbn"},
                {"name": "name"},
                {"name": "author", "include": False},
            ],
        }
        serializer = _serializer(_post(superuser, "preview", payload).data["data"])
        assert '"author"' not in serializer
        assert serializer.index('"isbn"') < serializer.index('"name"')

    def test_in_table_override_adds_engine_excluded_field(self, superuser):
        # publisher 是短文本（默认在表格列）；isbn max_length=20 也在候选面，
        # 选用长文本不会出现 —— 这里把默认不在表格列的 admin2 置顶并保持入列
        payload = {
            "model": "demo.Book",
            "fields": [{"name": "admin2", "in_table": True}],
        }
        serializer = _serializer(_post(superuser, "preview", payload).data["data"])
        table_block = serializer.split("table_fields = [", 1)[1].split("]", 1)[0]
        assert '"admin2"' in table_block

    def test_in_table_false_removes_from_table(self, superuser):
        payload = {
            "model": "demo.Book",
            "fields": [{"name": "name", "in_table": False}],
        }
        serializer = _serializer(_post(superuser, "preview", payload).data["data"])
        table_block = serializer.split("table_fields = [", 1)[1].split("]", 1)[0]
        assert '"name"' not in table_block
        # 但字段仍保留在序列化器字段面
        assert '"name"' in serializer

    def test_in_search_false_removes_from_filterset(self, superuser):
        payload = {
            "model": "demo.Book",
            "fields": [{"name": "name", "in_search": False}],
        }
        views = next(row for row in _post(superuser, "preview", payload).data["data"] if row["label"] == "视图")
        assert "name = filters.CharFilter" not in views["content"]
        assert '"name"' not in views["content"].split("fields = [", 1)[1].split("]", 1)[0]

    def test_input_type_override_on_relation(self, superuser):
        payload = {
            "model": "demo.Book",
            "fields": [{"name": "admin", "input_type": "object_related_field"}],
        }
        serializer = _serializer(_post(superuser, "preview", payload).data["data"])
        block = serializer.split('"admin": {', 1)[1].split("},", 1)[0]
        assert '"input_type": "object_related_field"' in block

    def test_input_type_on_non_relation_rejected(self, superuser):
        payload = {
            "model": "demo.Book",
            "fields": [{"name": "name", "input_type": "api-search-user"}],
        }
        response = _post(superuser, "preview", payload)
        assert response.data["code"] == 1001
        assert "关联字段" in response.data["detail"]

    def test_input_type_outside_vocabulary_rejected(self, superuser):
        payload = {
            "model": "demo.Book",
            "fields": [{"name": "admin", "input_type": "not-a-type"}],
        }
        response = _post(superuser, "preview", payload)
        assert response.data["code"] == 1001
        assert "not-a-type" in response.data["detail"]


class TestDictBinding:
    """字典绑定：DictChoiceField 显式声明；code 存在性与字段类型硬校验。"""

    @staticmethod
    def _create_dict(code):
        from system.models.dict import DataDict

        return DataDict.objects.create(code=code, label="测试字典")

    def test_bind_dict_generates_declaration(self, superuser):
        self._create_dict("book_category")
        payload = {
            "model": "demo.Book",
            "fields": [{"name": "category", "dict_code": "book_category"}],
        }
        serializer = _serializer(_post(superuser, "preview", payload).data["data"])
        assert "from common.core.fields_dict import DictChoiceField" in serializer
        assert 'category = DictChoiceField(dict_code="book_category", value_cast=int)' in serializer

    def test_unknown_dict_code_rejected(self, superuser):
        payload = {
            "model": "demo.Book",
            "fields": [{"name": "name", "dict_code": "no_such_dict"}],
        }
        response = _post(superuser, "preview", payload)
        assert response.data["code"] == 1001
        assert "no_such_dict" in response.data["detail"]

    def test_dict_on_relation_rejected(self, superuser):
        self._create_dict("user_kind")
        payload = {
            "model": "demo.Book",
            "fields": [{"name": "admin", "dict_code": "user_kind"}],
        }
        response = _post(superuser, "preview", payload)
        assert response.data["code"] == 1001
        assert "非关联字段" in response.data["detail"]

    def test_excluded_field_skips_dict_check(self, superuser):
        """排除字段的其他配置一律忽略：绑了字典也因排除而不校验、不生效。"""
        payload = {
            "model": "demo.Book",
            "fields": [{"name": "category", "include": False, "dict_code": "no_such_dict"}],
        }
        response = _post(superuser, "preview", payload)
        assert response.status_code == 200


class TestWithTestsOption:
    def test_with_tests_includes_skeleton(self, superuser):
        response = _post(superuser, "preview", {"model": "demo.Book", "with_tests": True})
        labels = [row["label"] for row in response.data["data"]]
        assert "测试骨架" in labels


class TestAdvancedOptions:
    """GUI 高级选项：菜单标题 / 排序覆盖 / AI 声明与前端产物开关。"""

    def test_menu_title_overrides_seed(self, superuser):
        response = _post(superuser, "preview", {"model": "demo.Book", "menu_title": "书籍管理"})
        seed = next(row for row in response.data["data"] if row["label"] == "菜单种子")
        assert '"title": "书籍管理"' in seed["content"]

    def test_ordering_override_in_views(self, superuser):
        response = _post(superuser, "preview", {"model": "demo.Book", "ordering": "isbn"})
        views = next(row for row in response.data["data"] if row["label"] == "视图")
        assert 'ordering = ["isbn"]' in views["content"]

    def test_invalid_ordering_rejected(self, superuser):
        response = _post(superuser, "preview", {"model": "demo.Book", "ordering": "bad name!"})
        assert response.data["code"] == 1001
        assert "ordering" in response.data["detail"]

    def test_skip_ai_omits_declaration(self, superuser):
        response = _post(superuser, "preview", {"model": "demo.Book", "with_ai": False})
        labels = [row["label"] for row in response.data["data"]]
        assert "AI 动作声明" not in labels

    def test_skip_frontend_omits_client_paths(self, superuser):
        response = _post(superuser, "preview", {"model": "demo.Book", "with_frontend": False})
        paths = [row["path"] for row in response.data["data"]]
        assert not any(path.startswith("xadmin-client/") for path in paths)


class TestDownload:
    def test_zip_contains_artifacts_at_repo_paths(self, superuser):
        response = _post(superuser, "download", {"model": "demo.Book"})
        assert response.status_code == 200
        assert response["Content-Type"] == "application/zip"
        assert "attachment" in response["Content-Disposition"]
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
        assert any(name.startswith("xadmin-server/demo/") for name in names)
        assert any(name.startswith("xadmin-client/src/views/demo/") for name in names)
        assert any(name.endswith("seed_demo_book.json") for name in names)

    def test_zip_skips_notice_artifacts(self, superuser, monkeypatch):
        def _fake_build(payload):
            return [
                {"label": "视图", "path": "xadmin-server/demo/views.py", "content": "x", "mode": "create", "key": "k"},
                {"label": "前端页面", "path": "", "content": "", "mode": "notice", "key": "f", "notice": "n"},
            ]

        monkeypatch.setattr(codegen_gui, "build_artifacts", _fake_build)
        response = _post(superuser, "download", {"model": "demo.Book"})
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert archive.namelist() == ["xadmin-server/demo/views.py"]

    def test_zip_contains_next_steps_markdown(self, superuser):
        response = _post(superuser, "download", {"model": "demo.Book"})
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            content = archive.read("NEXT_STEPS.md").decode("utf-8")
        assert "demo.Book（" in content
        assert "python manage.py loaddata loadjson/seed_demo_book.json" in content
        assert "python manage.py doctor" in content


class TestBatchDownload:
    """批量多模型打包：共享选项 + 逐模型默认字段计划 + 合并 NEXT_STEPS.md。"""

    def test_zip_covers_multiple_models(self, superuser):
        response = _post(superuser, "download", {"models": ["demo.Book", "system.DataDict"]})
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            next_steps = archive.read("NEXT_STEPS.md").decode("utf-8")
        assert any("demo/" in name for name in names)
        assert any("system/" in name for name in names)
        assert names.count("NEXT_STEPS.md") == 1
        assert "demo.Book（" in next_steps and "system.DataDict（" in next_steps

    def test_duplicate_paths_keep_first_with_notice(self, superuser):
        from pathlib import Path

        artifacts = [
            {"label": "路由", "path": Path("a/urls.py"), "content": "one", "mode": "urls", "key": "u1"},
            {"label": "路由", "path": Path("a/urls.py"), "content": "two", "mode": "urls", "key": "u2"},
            {"label": "路由", "path": Path("a/urls.py"), "content": "one", "mode": "urls", "key": "u3"},
        ]
        deduped = codegen_gui._dedupe_artifacts(artifacts)
        notices = [row for row in deduped if row.get("mode") == "notice"]
        assert len(notices) == 1
        assert "a/urls.py" in notices[0]["notice"]

    def test_unknown_model_in_batch_rejected(self, superuser):
        response = _post(superuser, "download", {"models": ["demo.Book", "nope.Missing"]})
        assert response.data["code"] == 1001
