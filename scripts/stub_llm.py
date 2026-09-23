#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""E2E 桩 LLM 服务器：OpenAI /v1/chat/completions 兼容的最小实现（无外部依赖）。

用途：E2E 环境没有真实 LLM（AI_BASE_URL 指向本桩），AI 动作草稿（A2）等链路
需要一个**确定性**的模型回答。由 playwright.config.ts 的 webServer 拉起，
端口 E2E_STUB_LLM_PORT（默认 18897）。

规则（与 system/utils/ai_actions.py 的 prompt 契约对齐）：
- 从最后一条 user 消息解析 ``ALLOWED_ACTIONS_JSON:`` 标记之后的动作目录；
- 目录中存在名字以 ``E2E-AI动作`` 开头的表单 → 返回 dform.submit 草稿
  （data 按表单字段逐个填固定值，input 字段可过校验）；
- 请求含「串联」关键字 → 返回公告 + 已读两步串联草稿（多动作契约覆盖）；
- 否则取用户消息中的首个 YYYY-MM-DD 日期（没有就用明天）→ 返回 leave.submit 草稿
  （E2E 用例在消息里携带唯一日期，保证多次运行不产生区间重叠）；
- 其余（/kb 问答、普通多轮）返回固定文案。
输出契约：多草稿 ``{"actions": [...]}``（新契约；服务端 parse 兼容旧单对象）。

端点：
- POST /v1/chat/completions（含 stream 字段也按整体回答返回，本桩只服务非流式调用；
  流式链路由 tests/integration/message/test_chat_stream.py 的进程内桩覆盖）
- GET /health → 200（playwright webServer 就绪探测）
"""

import datetime
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ALLOWED_ACTIONS_MARKER = "ALLOWED_ACTIONS_JSON:"
#: AI-6 护栏的引用数据块边界（目录被包裹后仍可解析；与 system/utils/ai_guard.py 同口径）
REFERENCE_BEGIN = "<<<REFERENCE_DATA>>>"
REFERENCE_END = "<<<END_REFERENCE_DATA>>>"
E2E_FORM_NAME_PREFIX = "E2E-AI动作"
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
FALLBACK_ANSWER = "这是 E2E 桩 LLM 的固定回答。"
#: 流式实时性探针：命中该关键字的请求返回长文本（足够切成多帧，验证增量到达）
STREAM_PROBE_MARKER = "E2E-STREAM-PROBE"
#: 受限动作探针：请求里出现的 E2E 目标用户名（禁用用户用例）
TARGET_USERNAME_RE = re.compile(r"e2e_ai_target_\d+")
LONG_ANSWER = "".join(f"这是流式探针的第 {index} 段输出，用于验证增量到达。" for index in range(1, 21))


def extract_catalog(text: str) -> dict:
    """从标记后的文本中解析动作目录：兼容 AI-6 引用数据块包裹（取首个 { 到末个 }）。"""
    if REFERENCE_BEGIN in text:
        text = text.split(REFERENCE_BEGIN, 1)[1]
    if REFERENCE_END in text:
        text = text.split(REFERENCE_END, 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def build_answer(messages: list) -> str:
    user_text = ""
    for message in reversed(messages or []):
        if message.get("role") == "user":
            user_text = str(message.get("content") or "")
            break

    # 动作草稿仅服务于 `/do` 草稿链路（prompt 携带动作目录标记）；文档问答 / NL
    # 解释等无标记请求一律返回固定问答文案（否则会被 NL 解析为未知结构）。
    if ALLOWED_ACTIONS_MARKER not in user_text:
        if STREAM_PROBE_MARKER in user_text:
            return LONG_ANSWER
        return FALLBACK_ANSWER

    # 意图判断只看用户原始请求（marker 之前的部分）：动作目录里的中文描述
    # （如"向全部用户发布一条系统公告"）会干扰关键字判断
    request_text = user_text.split(ALLOWED_ACTIONS_MARKER, 1)[0]
    catalog = extract_catalog(user_text.split(ALLOWED_ACTIONS_MARKER, 1)[1])

    # 动作草稿：优先命中 E2E 动作表单（按名字前缀）
    for action in catalog.get("actions", []) if isinstance(catalog, dict) else []:
        if action.get("action") != "dform.submit":
            continue
        for form in action.get("forms") or []:
            name = str(form.get("name") or "")
            if not name.startswith(E2E_FORM_NAME_PREFIX):
                continue
            data = {
                str(field.get("key")): f"{E2E_FORM_NAME_PREFIX}-{name}"
                for field in form.get("fields") or []
                if field.get("type") in (None, "input", "textarea")
            }
            return json.dumps(
                {
                    "actions": [
                        {
                            "action": "dform.submit",
                            "params": {"form_id": form.get("form_id"), "data": data, "form_name": name},
                            "summary": f"向表单 {name} 提交一条 E2E 记录",
                        }
                    ]
                },
                ensure_ascii=False,
            )

    # 多步串联草稿：请求含「串联」且目录具备公告 + 站内信动作 → 两步一起产出
    chained = "串联" in request_text and {
        "notice.publish",
        "message.mark_all_read",
    }.issubset({action.get("action") for action in catalog.get("actions", [])} if isinstance(catalog, dict) else set())
    if chained:
        return json.dumps(
            {
                "actions": [
                    {
                        "action": "notice.publish",
                        "params": {
                            "title": "E2E 串联公告",
                            "message": "大家好，这是 E2E 桩生成的串联公告内容。",
                            "level": "info",
                        },
                        "summary": "发布一条系统公告",
                    },
                    {"action": "message.mark_all_read", "params": {}, "summary": "标记全部消息已读"},
                ]
            },
            ensure_ascii=False,
        )

    # 只读查询草稿：请求含「用户数」且目录具备 dashboard 聚合动作（结果表渲染链路）
    if "用户数" in request_text and any(
        action.get("action") == "dashboard.overview" for action in catalog.get("actions", [])
    ):
        return json.dumps(
            {
                "actions": [
                    {
                        "action": "dashboard.overview",
                        "params": {},
                        "summary": "查询首页统计获取用户数量",
                    }
                ]
            },
            ensure_ascii=False,
        )

    # 公告草稿：目录含公告动作且用户请求出现"公告"关键字
    if "公告" in request_text and any(
        action.get("action") == "notice.publish" for action in catalog.get("actions", [])
    ):
        return json.dumps(
            {
                "actions": [
                    {
                        "action": "notice.publish",
                        "params": {
                            "title": "E2E 系统公告",
                            "message": "大家好，这是 E2E 桩生成的公告内容。",
                            "level": "info",
                        },
                        "summary": "发布一条系统公告",
                    }
                ]
            },
            ensure_ascii=False,
        )

    # 用户启停草稿：目录含 user.set_active 且请求里出现 E2E 目标用户名
    target_match = TARGET_USERNAME_RE.search(request_text)
    if target_match and any(action.get("action") == "user.set_active" for action in catalog.get("actions", [])):
        target = target_match.group(0)
        return json.dumps(
            {
                "actions": [
                    {
                        "action": "user.set_active",
                        "params": {"pk": target, "is_active": False},
                        "summary": f"禁用用户 {target}",
                    }
                ]
            },
            ensure_ascii=False,
        )

    # 请假草稿：日期取用户消息中的首个日期（E2E 保证唯一），缺省用明天
    matched = DATE_RE.search(request_text)
    if matched:
        start = datetime.date.fromisoformat(matched.group(0))
    else:
        start = datetime.date.today() + datetime.timedelta(days=1)
    reason = "E2E AI 动作请假"
    return json.dumps(
        {
            "actions": [
                {
                    "action": "leave.submit",
                    "params": {
                        "leave_type": "annual",
                        "start_date": start.isoformat(),
                        "end_date": start.isoformat(),
                        "days": 1,
                        "reason": reason,
                    },
                    "summary": f"提交 {start.isoformat()} 的年假申请",
                }
            ]
        },
        ensure_ascii=False,
    )


class StubLLMHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102 静默访问日志
        return

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, answer: str) -> None:
        """stream=true：OpenAI 兼容 SSE（reasoning 帧 + 正文分段 + [DONE]）。

        思考帧用于覆盖「思考过程展示」链路；正文按 8 字符切段模拟真实流式增量，
        帧间 10ms 模拟 token 节奏（E2E 的「增量到达」守护据此断言时间跨度）。
        HTTP/1.0 无 Content-Length：客户端读到连接关闭即结束。
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def frame(delta: dict) -> None:
            payload = {"choices": [{"index": 0, "delta": delta}]}
            self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.01)

        frame({"role": "assistant", "reasoning_content": "E2E 桩：正在分析问题…"})
        for index in range(0, len(answer), 8):
            frame({"content": answer[index : index + 8]})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def do_GET(self):  # noqa: N802
        if urlparse(self.path).path == "/health":
            self._send_json({"status": "ok", "ts": time.time()})
            return
        self._send_json({"detail": "not found"}, status=404)

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path != "/v1/chat/completions":
            self._send_json({"detail": "not found"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            answer = build_answer(payload.get("messages") or [])
        except Exception:  # noqa: BLE001 桩服务不允许 500 挂死 E2E
            payload, answer = {}, FALLBACK_ANSWER
        if isinstance(payload, dict) and payload.get("stream"):
            self._send_sse(answer)
            return
        self._send_json(
            {
                "id": "stub-llm",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": payload.get("model", "stub") if isinstance(payload, dict) else "stub",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        )


def main() -> None:
    import os

    port = int(os.environ.get("E2E_STUB_LLM_PORT") or 18897)
    server = ThreadingHTTPServer(("127.0.0.1", port), StubLLMHandler)
    print(f"stub llm listening on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
