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

覆盖：demo.BookViewSet（演示模型，字段形态最全）、system.UserViewSet（业务模型，
字段最复杂），以及 URLconf 递归收集到的全部带 ``search-columns`` / ``search-fields``
动作的视图集（新增类型逃逸即 CI 失败）。
"""

import json
from pathlib import Path

import pytest
from django.urls import get_resolver
from jsonschema import Draft7Validator
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.utils import encoders

from approval.views.leave import LeaveViewSet
from common.core.modelset.input_types import (
    DECLARED_INPUT_TYPES,
    FALLBACK_RENDERED_INPUT_TYPES,
    INPUT_TYPE_PREFIX_FAMILIES,
)
from demo.views import BookViewSet
from file.views.admin.file import UploadFileViewSet
from identity.views.admin.login_policy import LoginAccessPolicyViewSet
from identity.views.admin.user import UserViewSet
from settings.views.basic import BasicSettingViewSet

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


#: 无法脱离请求上下文构造的视图集（逐项写理由；保持最小，禁止用它掩盖未登记类型）
SKIPPED_METADATA_VIEWSETS: dict[str, str] = {}

_METADATA_ENDPOINTS = {"search_columns": "search-columns", "search_fields": "search-fields"}


def _collect_metadata_targets() -> list[dict]:
    """递归遍历 URLconf，收集全部暴露元数据动作的视图集（类去重）。

    只认路由回调里实际注册的动作（``callback.actions``），避免把仅继承 mixin
    但未挂路由的类纳入；完整路径仅用于构造请求，不做 URL resolve。
    """
    targets: dict[type, dict] = {}

    def walk(patterns, prefix: str) -> None:
        for pattern in patterns:
            part = str(getattr(pattern, "pattern", ""))
            if hasattr(pattern, "url_patterns"):
                walk(pattern.url_patterns, prefix + part)
                continue
            callback = getattr(pattern, "callback", None)
            cls = getattr(callback, "cls", None)
            actions = getattr(callback, "actions", None) or {}
            endpoints = {_METADATA_ENDPOINTS[action] for action in actions.values() if action in _METADATA_ENDPOINTS}
            if cls is None or not endpoints:
                continue
            entry = targets.setdefault(
                cls,
                {
                    "cls": cls,
                    "url": "/" + (prefix + part).lstrip("^").rstrip("$"),
                    "endpoints": set(),
                },
            )
            entry["endpoints"] |= endpoints

    walk(get_resolver().url_patterns, "")
    return list(targets.values())


def _metadata_payloads(target: dict, superuser) -> dict[str, list]:
    cls, url = target["cls"], target["url"]
    payloads: dict[str, list] = {}
    for action, endpoint in _METADATA_ENDPOINTS.items():
        if endpoint not in target["endpoints"]:
            continue
        payloads[endpoint] = _fetch_data_payload(cls, {"get": action}, f"{url.rstrip('/')}/{endpoint}", superuser)
    return payloads


def _emit_metadata(target: dict, superuser) -> set[tuple[str, str]]:
    emitted: set[tuple[str, str]] = set()
    for endpoint, payload in _metadata_payloads(target, superuser).items():
        emitted |= {(endpoint, item["input_type"]) for item in payload}
    return emitted


METADATA_TARGETS = _collect_metadata_targets()


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
    [
        (BookViewSet, "/api/demo/book"),
        (UserViewSet, "/api/identity/user"),
        # TimeField 覆盖：登录策略的 start_time/end_time 经 label_lookup 下发 "time"，
        # 该类型的词表 / Schema / 前端渲染器在扩展流程中同步补齐
        (LoginAccessPolicyViewSet, "/api/identity/login-policies"),
    ],
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


@pytest.mark.parametrize(
    "target",
    METADATA_TARGETS,
    ids=lambda target: target["cls"].__name__,
)
def test_all_metadata_viewsets_match_schema(target, superuser):
    """全量视图集载荷 Schema 校验：契约真源不仅锁词表，也锁键形状。

    「词表闭包」只保证 input_type 合法；本用例保证每个真实载荷符合
    ``docs/schema/*.schema.json``（含 child / predefine / decimal_places 等扩展键），
    防止新增键或嵌套字段展开在无人察觉时漂移出契约。
    """
    payloads = _metadata_payloads(target, superuser)
    assert payloads, "元数据载荷不应为空"
    for endpoint, payload in payloads.items():
        _assert_matches_schema(payload, f"{endpoint}.schema.json")


@pytest.mark.parametrize(
    "target",
    METADATA_TARGETS,
    ids=lambda target: target["cls"].__name__,
)
def test_all_metadata_viewsets_input_type_closure(target, superuser):
    """全量视图集闭包：URLconf 中每个暴露元数据动作的视图集都不许下发未登记类型。

    这是「后端定义了字段、前端没渲染」从线上现形提前到 CI 拦截的关键门禁；
    无法脱离请求上下文构造的视图集走 ``SKIPPED_METADATA_VIEWSETS``（逐项写理由）。
    """
    assert target["cls"].__name__ not in SKIPPED_METADATA_VIEWSETS, (
        f"{target['cls'].__name__} 已登记 skip：{SKIPPED_METADATA_VIEWSETS[target['cls'].__name__]}"
    )
    emitted = _emit_metadata(target, superuser)
    assert emitted, "元数据载荷不应为空"
    undeclared = {t for _, t in emitted if not t.startswith(INPUT_TYPE_PREFIX_FAMILIES)} - DECLARED_INPUT_TYPES
    assert not undeclared, (
        f"{target['cls'].__name__} 下发了未登记的 input_type：{sorted(undeclared)}——"
        "先在 packages/xadmin-common/common/core/modelset/input_types.py 词表登记（连同呈现归宿），"
        "再同步 docs/schema 枚举与前端渲染器（扩展流程）；确需豁免时在 SKIPPED_METADATA_VIEWSETS 写明理由"
    )


class TestRegisteredLongTailTypes:
    """存量长尾登记的回归锚点：url / decimal 必须持续以正式类型下发。"""

    def test_url_field_emits_registered_type(self, superuser):
        payload = _fetch_data_payload(
            BasicSettingViewSet, {"get": "search_columns"}, "/api/settings/basic/search-columns", superuser
        )
        site_url = next(item for item in payload if item["key"] == "SITE_URL")
        assert site_url["input_type"] == "url"

    def test_upload_file_url_emits_registered_type(self, superuser):
        payload = _fetch_data_payload(
            UploadFileViewSet, {"get": "search_columns"}, "/api/file/file/search-columns", superuser
        )
        file_url = next(item for item in payload if item["key"] == "file_url")
        assert file_url["input_type"] == "url"

    def test_decimal_field_emits_places_metadata(self, superuser):
        payload = _fetch_data_payload(
            LeaveViewSet, {"get": "search_columns"}, "/api/approval/leaves/search-columns", superuser
        )
        days = next(item for item in payload if item["key"] == "days")
        assert days["input_type"] == "decimal"
        assert days["decimal_places"] == 1
