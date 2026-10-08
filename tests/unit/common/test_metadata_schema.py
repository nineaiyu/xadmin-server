# -*- coding: utf-8 -*-
"""元数据接口契约测试。

search-columns / search-fields 的 data 载荷必须符合 docs/schema/ 下的
JSON Schema——这是前后端元数据协议（RePlusPage 渲染契约）的门禁。
Schema 变更属于破坏性契约变更，需同步前端生成类型并评审。

input_type 词表（稳定公共契约）由本模块两面守护：
- **载荷闭包**：真实端点下发的 input_type 必须落在声明词表（DECLARED_INPUT_TYPES
  ∪ api-* 前缀族）内——平台侧新增类型未登记即 fail；
- **Schema 锁步**：两份 Schema 的 input_type 枚举 / api-* pattern 例外 /
  x-fallback-rendered 回退登记与 ``packages/xadmin-common/common/core/modelset/input_types.py`` 真源
  逐一相等（真源改了 Schema 不同步即 fail，反之亦然）。

跨栈的「词表 ⇄ 前端渲染器注册表」双向覆盖对账在 client 仓库 vitest
（RePlusPage __tests__）执行，本模块不读 client 源码。

覆盖两个真实视图集：demo.BookViewSet（演示模型，字段形态最全）与
system.UserViewSet（业务模型，字段最复杂）。
"""

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.utils import encoders

from common.core.modelset.input_types import (
    DECLARED_INPUT_TYPES,
    FALLBACK_RENDERED_INPUT_TYPES,
    INPUT_TYPE_PREFIX_FAMILIES,
)
from demo.views import BookViewSet
from identity.views.admin.user import UserViewSet

pytestmark = pytest.mark.django_db

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "docs" / "schema"
METADATA_SCHEMAS = ("search-columns.schema.json", "search-fields.schema.json")


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
    assert not errors, "\n".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors)


def _input_type_schema_property(schema_name: str) -> dict:
    property_schema = _load_schema(schema_name)["items"]["properties"]["input_type"]
    assert property_schema.get("type") == "string"
    alternatives = property_schema.get("anyOf")
    assert alternatives and len(alternatives) == 2, "input_type 须为「封闭枚举 ∨ api-* 前缀族」双分支"
    return property_schema


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

    def test_input_type_vocabulary_closure(self, viewset_cls, base_url, superuser):
        """真实载荷 input_type 闭包：全部落在声明词表（封闭核心 ∪ api-* 族）内。

        平台侧新增可下发类型（DRF 长尾 / 自定义字段 / widget 覆写 / 业务显式声明）
        而未在 ``DECLARED_INPUT_TYPES`` 登记时，在此 fail——先登记再扩展。
        """
        emitted = {
            (endpoint, item["input_type"])
            for endpoint, action in (("search-fields", "search_fields"), ("search-columns", "search_columns"))
            for item in _fetch_data_payload(viewset_cls, {"get": action}, f"{base_url}/{endpoint}", superuser)
        }
        assert emitted, "元数据载荷不应为空"
        undeclared = {t for _, t in emitted if not t.startswith(INPUT_TYPE_PREFIX_FAMILIES)} - DECLARED_INPUT_TYPES
        assert not undeclared, (
            f"{viewset_cls.__name__} 下发了未登记的 input_type：{sorted(undeclared)}——"
            "先在 packages/xadmin-common/common/core/modelset/input_types.py 词表登记（连同呈现归宿），"
            "再同步 docs/schema 枚举与前端渲染器（扩展流程）"
        )


class TestVocabularyLockstep:
    """词表真源 ⇄ Schema 落盘锁步：单向漂移双向拦截。"""

    @pytest.mark.parametrize("schema_name", METADATA_SCHEMAS)
    def test_schema_enum_matches_declared_vocabulary(self, schema_name):
        property_schema = _input_type_schema_property(schema_name)
        enum_branch, pattern_branch = property_schema["anyOf"]
        assert set(enum_branch["enum"]) == set(DECLARED_INPUT_TYPES), (
            f"{schema_name} input_type 枚举与 DECLARED_INPUT_TYPES 真源不一致——"
            "两处须同批修改（词表扩展流程见 / input_types.py 模块注释）"
        )
        assert pattern_branch.get("pattern") == "^api-" and pattern_branch.get("type") == "string", (
            "api-* 前缀族例外分支须保持 string + ^api-（type 显式声明使生成 TS 类型收敛为 string——"
            "框架边界对业务自定义类型保持开放，穷尽性对账由 client vitest 承担）"
        )
        assert set(property_schema["x-fallback-rendered"]) == set(FALLBACK_RENDERED_INPUT_TYPES), (
            "x-fallback-rendered 回退登记与真源不一致"
        )
        assert set(FALLBACK_RENDERED_INPUT_TYPES) <= set(DECLARED_INPUT_TYPES), "回退登记必须是词表子集"

    def test_prefix_family_guard_nonempty(self):
        assert INPUT_TYPE_PREFIX_FAMILIES == ("api-",), "api-* 族例外与 Schema pattern / client 守护强耦合"
