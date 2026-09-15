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
- 否则取用户消息中的首个 YYYY-MM-DD 日期（没有就用明天）→ 返回 leave.submit 草稿
  （E2E 用例在消息里携带唯一日期，保证多次运行不产生区间重叠）；
- 其余（/kb 问答、普通多轮）返回固定文案。

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
E2E_FORM_NAME_PREFIX = "E2E-AI动作"
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
FALLBACK_ANSWER = "这是 E2E 桩 LLM 的固定回答。"


def build_answer(messages: list) -> str:
    user_text = ""
    for message in reversed(messages or []):
        if message.get("role") == "user":
            user_text = str(message.get("content") or "")
            break

    catalog = {}
    if ALLOWED_ACTIONS_MARKER in user_text:
        try:
            catalog = json.loads(user_text.split(ALLOWED_ACTIONS_MARKER, 1)[1].strip())
        except json.JSONDecodeError:
            catalog = {}

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
                    "action": "dform.submit",
                    "params": {"form_id": form.get("form_id"), "data": data, "form_name": name},
                    "summary": f"向表单 {name} 提交一条 E2E 记录",
                },
                ensure_ascii=False,
            )

    # 请假草稿：日期取用户消息中的首个日期（E2E 保证唯一），缺省用明天
    matched = DATE_RE.search(user_text)
    if matched:
        start = datetime.date.fromisoformat(matched.group(0))
    else:
        start = datetime.date.today() + datetime.timedelta(days=1)
    reason = "E2E AI 动作请假"
    return json.dumps(
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
            answer = FALLBACK_ANSWER
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
