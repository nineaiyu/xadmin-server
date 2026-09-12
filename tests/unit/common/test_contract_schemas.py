# -*- coding: utf-8 -*-
"""前后端协议契约测试（ApiResponse 信封 / 动态路由载荷 / WebSocket 帧）。

docs/schema/ 下协议级 Schema 的门禁：真实接口响应与真实 WS 出站帧必须符合
Schema——协议键集合或形状变化时在此显式失败，强制同步 client 镜像
（contract/schema/，check:contract 校验镜像一致），避免双端协议静默漂移。
"""

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.utils import encoders

from demo.views import BookViewSet
from message.base import AsyncJsonWebsocket
from message.protocol import MessageAction
from system.views.routes import UserRoutesAPIView

pytestmark = pytest.mark.django_db

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "docs" / "schema"


def _load_schema(name: str) -> dict:
    with open(SCHEMA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _wire(payload):
    """按 DRF 的 JSON 编码器转为线上格式（惰性翻译 proxy 会在此变成字符串）。"""
    return json.loads(json.dumps(payload, cls=encoders.JSONEncoder))


def _assert_matches_schema(payload, schema: dict):
    errors = sorted(Draft7Validator(schema).iter_errors(payload), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors)


class TestApiResponseEnvelope:
    def test_list_response_matches_envelope(self, superuser):
        """任意 ApiResponse 接口（demo.BookViewSet list）响应符合统一信封。"""
        factory = APIRequestFactory()
        request = factory.get("/api/demo/book")
        force_authenticate(request, user=superuser)
        response = BookViewSet.as_view({"get": "list"})(request)
        assert response.status_code == 200, response.data
        _assert_matches_schema(_wire(response.data), _load_schema("api-response.schema.json"))


class TestRoutesPayload:
    def test_routes_response_matches_schema(self, superuser):
        """动态路由接口完整响应（信封 + 路由树 + auths）符合契约。"""
        factory = APIRequestFactory()
        request = factory.get("/api/routes")
        force_authenticate(request, user=superuser)
        response = UserRoutesAPIView.as_view()(request)
        assert response.status_code == 200, response.data
        _assert_matches_schema(_wire(response.data), _load_schema("routes-payload.schema.json"))
        # auths 为权限码列表（无权限菜单的用户合法地是空数组），序列化后必须是 list
        assert isinstance(json.loads(json.dumps(list(response.data["auths"]))), list)


class _FrameRecorder:
    """最小消费替身：只捕获 send_base_json 组装出的帧内容。"""

    def __init__(self):
        self.frames = []

    async def send_json(self, content, close=False):
        self.frames.append(content)

    send_base_json = AsyncJsonWebsocket.send_base_json


class TestWsFrame:
    def test_outbound_frame_matches_schema(self):
        """真实出站帧（send_base_json 组装）符合协议 v1 帧 Schema。"""
        recorder = _FrameRecorder()
        asyncio.run(
            recorder.send_base_json(
                MessageAction.TASK_LOG.value,
                {"offset": 0, "content": "", "finished": False},
            )
        )
        frame = recorder.frames[0]
        schema = _load_schema("ws-frame.schema.json")
        _assert_matches_schema(_wire(frame), schema)
        # task_log 载荷键集合封闭，单独校验 data
        _assert_matches_schema(
            _wire(frame["data"]),
            {
                "$ref": "#/definitions/taskLogPayload",
                "definitions": schema["definitions"],
            },
        )

    def test_inbound_frame_matches_schema(self):
        """上行帧（客户端 → 服务端）键集合封闭且 action 受枚举约束。"""
        schema = _load_schema("ws-frame.schema.json")
        _assert_matches_schema({"action": "ping", "v": 1}, schema)
        # 未知 action 必须失败（新增 action 需双端同步登记后再放行）
        errors = list(Draft7Validator(schema).iter_errors({"action": "unknown", "v": 1}))
        assert errors, "未知 action 应被 Schema 拒绝"
