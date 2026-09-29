# -*- coding:utf-8 -*-
"""代码生成器 GUI 端点守护测试（适配层 system/utils/codegen_gui.py）。

覆盖：模型清单过滤（仓库内 / 非抽象）、字段计划回显、字段选择收敛
（include/exclude 与未知字段拒绝）、产物预览（不落盘）、zip 下载。
"""

import io
import zipfile

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from system.utils import codegen_gui
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
        paths = [row["path"] for row in artifacts]
        assert any(path.startswith("xadmin-server/demo/") for path in paths)
        assert any(path.startswith("xadmin-client/src/views/demo/") for path in paths)
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
