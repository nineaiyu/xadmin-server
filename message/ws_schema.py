#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WebSocket 帧协议 JSON Schema 真源（生成 ``docs/schema/ws-frame.schema.json``）。

产物由 ``scripts/gen_ws_frame_schema.py`` 渲染，请勿手工编辑 schema 文件；
一致性由 ``tests/unit/message/test_ws_frame_schema.py`` 在 CI 保证（漂移即失败）。

单源结构（消除此前的"枚举/TypedDict ↔ schema 手工双写"）：

- action 枚举由 ``message.protocol.MessageAction`` 生成——新增动作只改枚举；
- payload 的 properties 键集合与类型由 ``message/protocol.py`` 对应 TypedDict
  经 ``typing.get_type_hints`` 自动导出（改字段名/类型后重跑生成脚本即同步）；
- payload 的 required / additionalProperties / 字段描述 / 载荷所在通道说明无法由
  TypedDict 表达，在 ``PAYLOAD_DECLARATIONS`` 里显式声明（集中一处，非双写）。

新增 action 的完整动作：① ``protocol.py`` 登记枚举；② 补 Payload TypedDict；
③ 在 ``PAYLOAD_DECLARATIONS`` 登记 definition（含 required 与描述）；④ 重跑
``python scripts/gen_ws_frame_schema.py`` 并在 client 仓 ``pnpm sync:contract``。
"""

import json
from typing import Any, get_args, get_origin, get_type_hints

from message import protocol

SCHEMA_ID = "xadmin/schemas/ws-frame.schema.json"
SCHEMA_TITLE = "WebSocketFrameV1"
SCHEMA_DESCRIPTION = (
    "WebSocket 消息协议 v1 帧（生成物：真源为 message/protocol.py 与 message/ws_schema.py）。"
    "上行帧仅需 action；出站帧由 send_base_json 统一携带 code/detail/timestamp/v。"
    "新增 action 必须：① 双端 MessageAction 同步登记；② 补对应 payload 定义"
    "（message/ws_schema.py 的 PAYLOAD_DECLARATIONS）。"
    "服务端由 tests/unit/common/test_contract_schemas.py 与 tests/unit/message/test_ws_frame_schema.py 持续校验。"
)

#: Python 标量类型 → JSON Schema type
_SCALAR_TYPES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}

#: 帧壳（结构与字段说明固定；action 经 $ref 指向枚举，data 载荷由具体 action 决定）
_INBOUND_FRAME: dict[str, Any] = {
    "type": "object",
    "description": "客户端 → 服务端帧（客户端受控，键集合封闭）",
    "required": ["action"],
    "properties": {
        "action": {"$ref": "#/definitions/action"},
        "data": {"description": "载荷，由具体 action 决定"},
        "mid": {"type": "string", "description": "客户端回显一致性标记"},
        "v": {"type": "integer", "const": protocol.PROTOCOL_VERSION},
    },
    "additionalProperties": False,
}

_OUTBOUND_FRAME: dict[str, Any] = {
    "type": "object",
    "description": "服务端 → 客户端帧；服务端可经 send_base_json kwargs 追加顶层键，故不封闭 additionalProperties",
    "required": ["code", "action", "detail", "timestamp", "v"],
    "properties": {
        "code": {"type": "integer", "description": "业务码，语义同 ApiResponse.code"},
        "action": {"$ref": "#/definitions/action"},
        "detail": {"type": "string"},
        "timestamp": {"type": "string"},
        "data": {"description": "载荷，由具体 action 决定"},
        "mid": {"type": "string"},
        "v": {"type": "integer", "const": protocol.PROTOCOL_VERSION},
    },
    "additionalProperties": True,
}

#: payload definition 声明：definition 名 → 声明体
#:
#: - ``typed_dict``：protocol.py 中的 TypedDict 名（properties 键集合与类型由它导出）；
#: - ``required``：必填键（可严于 TypedDict 声明——运行期实际发送必带的键在此登记）；
#: - ``open``：是否允许额外键（additionalProperties）；
#: - ``fields``：字段描述（TypedDict 无法承载，缺省无描述）。
PAYLOAD_DECLARATIONS: dict[str, dict[str, Any]] = {
    "taskLogPayload": {
        "typed_dict": "TaskLogPayload",
        "description": "task_log 增量帧载荷（system/ws.py push_once，前端 TaskLogDialog 增量渲染契约）",
        "required": ["offset", "content", "finished"],
        "open": False,
        "fields": {
            "offset": "已推送字节偏移",
            "content": "本轮新增内容（空串表示无新增）",
            "finished": "读到结束标记或执行已终态",
        },
    },
    "chatRoomMessagePayload": {
        "typed_dict": "ChatRoomMessagePayload",
        "description": "聊天室消息帧载荷（ws/chat/ 通道 ChatNotify 落库后广播；与历史 ws/message/* 通道的 chat_message 载荷不同）",
        "required": ["id", "room_id", "content"],
        "open": True,
        "fields": {
            "id": "消息自增主键（游标）",
            "room_type": "public / private / ai",
            "message_type": "text / ai / system / image / video / audio / file",
            "client_msg_id": "客户端幂等键",
        },
    },
    "chatReactionPayload": {
        "typed_dict": "ChatReactionPayload",
        "description": "表情回应上行帧载荷（ws/chat/ 通道 ChatNotify；对消息 add/remove 自己的 emoji 回应，落库后广播全量表）",
        "required": ["message", "emoji", "op"],
        "open": False,
        "fields": {
            "message": "目标消息 pk（ChatMessage 自增主键）",
            "emoji": "回应表情（去首尾空白后 1-16 字符）",
            "op": "add 添加 / remove 移除（remove 只移除自己，不能替他人移除）",
        },
    },
    "chatReactionUpdatePayload": {
        "typed_dict": "ChatReactionUpdatePayload",
        "description": "表情回应广播帧载荷（ws/chat/ 下行；reactions 为该消息全量回应表，客户端整体替换即可）",
        "required": ["room", "message", "reactions", "ts"],
        "open": False,
        "fields": {
            "room": "房间 pk",
            "message": "消息 pk",
            "reactions": "全量回应表 {emoji: [user_pk, ...]}（整体替换，幂等）",
            "ts": "广播时刻（epoch 秒），客户端可据此丢弃乱序到达的旧帧",
        },
    },
    "screenCommandPayload": {
        "typed_dict": "ScreenCommandPayload",
        "description": "大屏远程控制帧载荷（ws/screen/<pk> 下行，dataset/ws_screen.py 广播；展示端被动接收）",
        "required": ["command"],
        "open": False,
        "fields": {
            "command": "switch / page / refresh / auto；连接回放时为 state",
            "mode": "auto 自动轮播 / manual 远程控制停播",
            "index": "当前页码（0 基）",
            "refresh_rev": "数据刷新代数（递增即重拉数据）",
            "rev": "控制态版本号（单调递增）",
            "ts": "指令落态时间（ISO）",
        },
    },
    "screenDataPayload": {
        "typed_dict": "ScreenDataPayload",
        "description": (
            "大屏聚合数据帧载荷（ws/screen/<pk> 下行；按观察者权限各自聚合后自推，"
            "dataset/ws_screen.py 的 screen_data_trigger，不做组广播）"
        ),
        "required": ["screen", "dashboard", "rev", "cards", "errors", "ts"],
        "open": False,
        "fields": {
            "screen": "大屏 pk",
            "dashboard": "所属仪表盘 pk（canvas 画布模式为 null；carousel 轮播模式为展示连接上报的当前页）",
            "rev": "控制态版本号（取当前控制态缓存 rev，未下发过指令为 0）",
            "cards": "卡片数据 {card, kind: execute|aggregate, data}；data 为 execute/aggregate 返回结构",
            "errors": "失败卡片 {card, detail}：数据集被删 / 字段权限 fail-closed 等，不影响其余卡片",
            "ts": "推送时刻（epoch 秒）",
        },
    },
    "screenPageStatePayload": {
        "typed_dict": "ScreenPageStatePayload",
        "description": (
            "大屏展示端当前页上报帧载荷（ws/screen/<pk> 上行，dataset/ws_screen.py 接收；"
            "carousel 触发聚合按上报页取数，避免整屏逐页聚合白跑查询——T02-08）"
        ),
        "required": ["index"],
        "open": False,
        "fields": {
            "index": "当前页码（0 基，按 Screen.dashboards 原序；canvas 模式不上报，非法/越界回退全页聚合）",
        },
    },
}


def _json_type(annotation: Any) -> dict:
    """Python 类型注解 → JSON Schema 类型片段（不支持的类型退化为无约束）。"""
    if annotation is Any:
        return {}
    if annotation is type(None):  # noqa: E721 类型对象比较
        return {"type": "null"}
    if annotation in _SCALAR_TYPES:
        return {"type": _SCALAR_TYPES[annotation]}
    origin = get_origin(annotation)
    if origin is not None:
        args = get_args(annotation)
        # X | None（含 Optional[X]）：产出 ["<type>", "null"]
        if type(None) in args:
            inner = [arg for arg in args if arg is not type(None)]
            parts: list[str] = []
            for arg in inner:
                piece = _json_type(arg)
                value = piece.get("type")
                if isinstance(value, str):
                    parts.append(value)
                elif isinstance(value, list):
                    parts.extend(value)
                else:
                    return {}
            parts.append("null")
            return {"type": parts[0]} if len(parts) == 1 else {"type": parts}
        if origin is dict:
            return {"type": "object"}
        if origin in (list, set, tuple):
            return {"type": "array"}
    return {}


def build_payload_definition(name: str) -> dict:
    """按声明 + TypedDict 导出单个 payload definition。"""
    declaration = PAYLOAD_DECLARATIONS[name]
    typed_dict = getattr(protocol, declaration["typed_dict"])
    annotations = get_type_hints(typed_dict)
    descriptions = declaration.get("fields") or {}
    properties: dict[str, Any] = {}
    for field, annotation in annotations.items():
        piece = _json_type(annotation)
        if descriptions.get(field):
            piece["description"] = descriptions[field]
        properties[field] = piece
    return {
        "type": "object",
        "description": declaration["description"],
        "required": list(declaration["required"]),
        "properties": properties,
        "additionalProperties": bool(declaration.get("open")),
    }


def build_schema() -> dict:
    """构建完整 schema（确定性输出：键序稳定，可逐字节比对落盘文件）。"""
    definitions: dict[str, Any] = {
        "action": {
            "type": "string",
            "enum": [action.value for action in protocol.MessageAction],
            "description": "消息动作枚举（MessageAction）",
        },
        "inboundFrame": _INBOUND_FRAME,
        "outboundFrame": _OUTBOUND_FRAME,
    }
    for name in PAYLOAD_DECLARATIONS:
        definitions[name] = build_payload_definition(name)
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": SCHEMA_ID,
        "title": SCHEMA_TITLE,
        "description": SCHEMA_DESCRIPTION,
        "definitions": definitions,
        "oneOf": [
            {"$ref": "#/definitions/inboundFrame"},
            {"$ref": "#/definitions/outboundFrame"},
        ],
    }


def render() -> str:
    """渲染 schema 文本（确定性输出：键序稳定、2 空格缩进、末尾换行）。

    生成脚本与守护测试共用本函数，保证"落盘 == 渲染"判定口径单源。
    """
    return json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n"
