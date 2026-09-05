# -*- coding: utf-8 -*-
"""元数据接口契约测试（T2.3）。

search-columns / search-fields 的 data 载荷必须符合 docs/schema/ 下的
JSON Schema——这是前后端元数据协议（RePlusPage 渲染契约）的门禁。
Schema 变更属于破坏性契约变更，需同步前端生成类型并评审。

覆盖两个真实视图集：demo.BookViewSet（演示模型，字段形态最全）与
system.UserViewSet（业务模型，字段最复杂）。
"""
import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.utils import encoders

from demo.views import BookViewSet
from system.views.admin.user import UserViewSet

pytestmark = pytest.mark.django_db

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "docs" / "schema"


def _load_schema(name: str) -> dict:
    with open(SCHEMA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _fetch_data_payload(viewset_cls, action_map, url, superuser):
    factory = APIRequestFactory()
    request = factory.get(url)
    force_authenticate(request, user=superuser)
    response = viewset_cls.as_view(action_map)(request)
    assert response.status_code == 200, response.data
    assert response.data["code"] == 1000
    return response.data["data"]


def _assert_matches_schema(payload, schema_name: str):
    # 先按 DRF 的 JSON 编码器转为线上格式（惰性翻译 proxy 会在此变成字符串）
    wire_payload = json.loads(json.dumps(payload, cls=encoders.JSONEncoder))
    schema = _load_schema(schema_name)
    errors = sorted(Draft7Validator(schema).iter_errors(wire_payload), key=lambda e: list(e.path))
    assert not errors, "\n".join(
        f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors
    )


@pytest.mark.parametrize(
    "viewset_cls,base_url",
    [(BookViewSet, "/api/demo/book"), (UserViewSet, "/api/system/user")],
)
class TestMetadataContract:
    def test_search_fields_matches_schema(self, viewset_cls, base_url, superuser):
        payload = _fetch_data_payload(viewset_cls, {"get": "search_fields"}, f"{base_url}/search-fields", superuser)
        assert isinstance(payload, list) and payload
        _assert_matches_schema(payload, "search-fields.schema.json")

    def test_search_columns_matches_schema(self, viewset_cls, base_url, superuser):
        payload = _fetch_data_payload(viewset_cls, {"get": "search_columns"}, f"{base_url}/search-columns", superuser)
        assert isinstance(payload, list) and payload
        _assert_matches_schema(payload, "search-columns.schema.json")

    def test_search_columns_keys_are_unique(self, viewset_cls, base_url, superuser):
        payload = _fetch_data_payload(viewset_cls, {"get": "search_columns"}, f"{base_url}/search-columns", superuser)
        keys = [item["key"] for item in payload]
        assert len(keys) == len(set(keys))
