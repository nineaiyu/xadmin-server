# -*- coding: utf-8 -*-
"""路径 / 缓存键 / 文件名的防御纵深守护（Flower 代理 / codegen zip / swagger 缓存 / 上传净化）。"""

import pytest

from common.celery.flower import _guard_flower_path
from file.utils.upload_store import sanitize_filename


class TestFlowerProxyPathGuard:
    @pytest.mark.parametrize(
        "path",
        ["tasks", "task/abc-123", "workers/info.json", "api/flower/tasks"],
    )
    def test_safe_paths_pass(self, path):
        assert _guard_flower_path(path) == path.strip("/")

    @pytest.mark.parametrize(
        "path",
        ["../etc/passwd", "tasks/../../secret", "tasks?x=1", "tasks#frag", "tasks/other host"],
    )
    def test_traversal_and_injection_rejected(self, path):
        with pytest.raises(ValueError):
            _guard_flower_path(path)


class TestCodegenZipFallbackPath:
    def test_outside_root_falls_back_to_single_segment(self, tmp_path):
        """root 外路径（含绝对路径）兜底为单级名，不进 zip 条目原样（防 zip-slip）。"""
        from pathlib import Path

        from django.conf import settings

        from devtools.services.codegen.gui import _relative_path

        outside = Path(tmp_path) / "somewhere" / "evil.py"
        result = _relative_path(outside)
        assert result == "generated/evil.py"
        assert settings.PROJECT_DIR not in result


class TestSwaggerCacheKeyLanguageDimension:
    def test_cache_key_contains_language(self):
        """schema 缓存键含语言维度：同用户跨语言不得共享缓存。"""
        from common.swagger.views import SchemaMixin

        class _View(SchemaMixin):
            pass

        class _Method:
            __name__ = "get"

        class _Request:
            class user:
                pk = 7

        key = _View().get_cache_key(_View(), _Method, _Request(), (), {})
        assert key == "_View_get_7_zh-cn" or key.endswith("_7_" + key.rsplit("_", 1)[-1])
        # 键由三段构成（视图_方法_用户_语言），语言段非空
        assert len(key.rsplit("_", 1)[-1]) > 0


class TestUploadFilenameSanitizer:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("report.pdf", "report.pdf"),
            ("../../etc/passwd", "passwd"),
            ("a/b/c.txt", "c.txt"),
        ],
    )
    def test_path_separators_and_traversal_stripped(self, raw, expected):
        """净化器剥离路径分隔符与遍历段（上传校验/导出与报表文件名共用）。"""
        assert sanitize_filename(raw) == expected

    @pytest.mark.parametrize("raw", ["..", ".", ""])
    def test_degenerate_names_fall_back_to_readable_default(self, raw):
        """退化为目录引用的文件名回落可读默认名，不产出空路径段。"""
        assert sanitize_filename(raw)
