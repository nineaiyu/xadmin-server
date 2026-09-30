# -*- coding: utf-8 -*-
"""ws-frame schema 生成物守护。

docs/schema/ws-frame.schema.json 由 message/ws_schema.py（枚举 + TypedDict 单源）
经 scripts/gen_ws_frame_schema.py 渲染——本测试把三件事钉死：

1. 落盘文件 == 真源渲染（手改 schema 即失败，必须跑生成脚本）；
2. action 枚举由 MessageAction 自动生成（新增动作只改枚举）；
3. 每个 payload definition 的 properties 键集合与 protocol.py 对应 TypedDict
   对账（TypedDict 改字段后不同步重生成即失败）。
"""

import json
from pathlib import Path

from message import protocol
from message.ws_schema import PAYLOAD_DECLARATIONS, render

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "docs" / "schema" / "ws-frame.schema.json"


def _load() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


class TestSchemaIsGenerated:
    def test_file_matches_render(self):
        """落盘文件与真源渲染逐字节一致（禁止手改 schema 文件）。"""
        assert SCHEMA_PATH.read_text(encoding="utf-8") == render()

    def test_action_enum_follows_message_action(self):
        """action 枚举由 MessageAction 生成（新增动作自动进入契约）。"""
        schema = _load()
        assert schema["definitions"]["action"]["enum"] == [action.value for action in protocol.MessageAction]


class TestPayloadDefinitions:
    def test_properties_match_typed_dicts(self):
        """payload definition 的 properties 键集合 == 对应 TypedDict 注解键集合（双向）。"""
        schema = _load()
        for name, declaration in PAYLOAD_DECLARATIONS.items():
            typed_dict = getattr(protocol, declaration["typed_dict"])
            definition = schema["definitions"][name]
            assert sorted(definition["properties"]) == sorted(typed_dict.__annotations__), (
                f"{name} 与 {declaration['typed_dict']} 字段集合不一致："
                f"仅 schema 有 {sorted(set(definition['properties']) - set(typed_dict.__annotations__))}；"
                f"仅 TypedDict 有 {sorted(set(typed_dict.__annotations__) - set(definition['properties']))}"
            )

    def test_required_is_subset_of_properties(self):
        """required 必须是已声明键的子集（TypedDict 无法表达 required，如有意严于声明在此校验）。"""
        schema = _load()
        for name, definition in schema["definitions"].items():
            properties = definition.get("properties")
            if properties is None:
                continue
            required = definition.get("required") or []
            assert set(required) <= set(properties), f"{name} 的 required 含未声明键"
