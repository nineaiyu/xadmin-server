# -*- coding: utf-8 -*-
"""OpenAPI 导出稳定性处理单测。"""

import json
from pathlib import Path

from devtools.services.openapi import is_runtime_default, normalize_schema
from server.const import PROJECT_DIR, VERSION


class TestRuntimeDefault:
    def test_uuid_and_datetime_are_runtime_defaults(self):
        assert is_runtime_default("95b94c74-9a53-4dbe-8aa2-56950478fdb2")
        assert is_runtime_default("2026-10-22 21:51:11")
        assert is_runtime_default("2026-10-22T21:51:11+08:00")

    def test_literal_defaults_are_stable(self):
        assert not is_runtime_default("pending")
        assert not is_runtime_default(0)
        assert not is_runtime_default(None)


class TestNormalizeSchema:
    def test_runtime_defaults_removed_recursively(self):
        schema = {
            "components": {
                "schemas": {
                    "Thing": {
                        "properties": {
                            "pk": {"type": "string", "default": "95b94c74-9a53-4dbe-8aa2-56950478fdb2"},
                            "status": {"type": "string", "default": "pending"},
                        }
                    }
                }
            }
        }

        normalized = normalize_schema(schema)

        props = normalized["components"]["schemas"]["Thing"]["properties"]
        assert "default" not in props["pk"]
        assert props["status"]["default"] == "pending"

    def test_input_not_mutated(self):
        schema = {"default": "95b94c74-9a53-4dbe-8aa2-56950478fdb2"}
        normalize_schema(schema)
        assert schema["default"] == "95b94c74-9a53-4dbe-8aa2-56950478fdb2"


class TestCheckedInSchema:
    def test_version_tracks_server_version(self):
        """版本号变更后未重导出 schema 时，前端类型横幅仍显示旧版本。"""
        path = Path(PROJECT_DIR) / "docs" / "schema" / "openapi.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["info"]["version"] == VERSION
