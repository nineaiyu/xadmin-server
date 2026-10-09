# -*- coding: utf-8 -*-
"""AI 答案质量回归集（规则式判分）：问答 / NL 解释链路的「回答内容」守护。

与检索评测（``tests/integration/ai/test_ai_retrieval_eval.py`` +
``tests/data/ai_retrieval_eval.json``，真实语料 hit@5）互补，但**不合并文件**：

- 检索评测：语料召回是否命中（期望文档是否进 top-5）；
- 本评测：链路装配出的**回答**是否携带关键内容、引用是否与检索一致、兜底文案是否正确。

同一「评测驱动」口径：**判分逻辑（``judge_answer``）与评测数据
（``tests/data/ai_answer_eval.json``）分离**——模型内容变化只改数据，链路退化才让用例
失败（避免把内容漂移误判为回归）。数据与受控语料的一致性由
``test_eval_data_is_self_consistent`` 单独守护。

CI 无真实模型：用进程内确定性「接地」StubLLM——回答内容由**检索装配的引用块**决定
（取首块正文作答），因此检索 / prompt 装配退化会改变回答并被判分器捕获；空回答与
「只思考未回答」等兜底分支用其它桩模式覆盖。脱敏护栏在响应装配侧，受控语料不含敏感形态。
"""

import json
from pathlib import Path
from typing import Any

import pytest
from django.utils.translation import gettext

from ai.models.ai import AiKnowledgeChunk

pytestmark = pytest.mark.django_db

EVAL_FILE = Path(__file__).resolve().parents[2] / "data" / "ai_answer_eval.json"

REFERENCE_BEGIN = "<<<REFERENCE_DATA>>>"
REFERENCE_END = "<<<END_REFERENCE_DATA>>>"

ASSISTANT_URL = "/api/ai/assistant"
INTERPRET_URL = f"{ASSISTANT_URL}/nl-query/interpret"

#: 受控知识库语料（docs 类用例）：期望短语 / 出处与 ai_answer_eval.json 双向一致
KNOWLEDGE_DOCS = [
    (
        "docs/answer-eval/dataset.md",
        "数据集执行",
        "数据集执行时按调用者数据权限过滤，未授权的行不会出现在查询结果中。"
        "数据集绑定白名单模型，字段超出白名单会被拒绝。",
    ),
    (
        "docs/answer-eval/report.md",
        "定时报表",
        "定时报表按 cron 表达式触发，运行完成后把 xlsx 附件通过邮件发送给收件人。",
    ),
    (
        "docs/answer-eval/watermark.md",
        "页面水印",
        "页面水印在配置页面上叠加用户名与时间戳，透明度可通过配置调整。水印不拦截截图。",
    ),
]

#: NL 类用例的数据集（绑定身份模型白名单）
DATASET_NAME = "用户清单"


def _load_eval() -> dict:
    return json.loads(EVAL_FILE.read_text(encoding="utf-8"))


CASES = _load_eval()["cases"]


# --------------------------------------------------------------------- 判分器（与数据分离）


def judge_answer(case: dict, answer: str, sources: Any) -> list[str]:
    """规则式判分：返回失败原因列表（空 = 通过）。

    判分器只认评测集里的判定要素，不含任何用例专属文案——内容变化改数据即可，
    无需改判分逻辑（判分器与数据分离）。
    """
    failures: list[str] = []
    answer = answer or ""
    if not answer.strip():
        failures.append("回答为空")
    for phrase in case.get("required_phrases") or []:
        if phrase not in answer:
            failures.append(f"缺少关键短语：{phrase}")
    for phrase in case.get("forbidden_phrases") or []:
        if phrase in answer:
            failures.append(f"命中黑名单短语：{phrase}")
    expected = case.get("expected_sources") or []
    if expected:
        paths = [item.get("path") for item in (sources or [])]
        if not set(expected) & set(paths):
            failures.append(f"引用出处不含期望：expected={expected} got={paths}")
    return failures


# --------------------------------------------------------------------- 接地 StubLLM


def _first_block(prompt: str) -> str:
    """取 user prompt 首块引用正文（兼容护栏开关两种形态：包裹 / 裸文）。"""
    context = (prompt or "").split("\n\n---\n", 1)[0]
    collected: list[str] = []
    inside = False
    for line in context.splitlines():
        if line.startswith(REFERENCE_BEGIN):
            if inside:
                break
            inside = True
            continue
        if line.startswith(REFERENCE_END):
            if inside:
                break
            continue
        if inside:
            collected.append(line)
    if collected:
        return "\n".join(collected).strip()
    return (context.split("\n\n", 1)[0] or "").strip()


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class GroundedStubLLM:
    """确定性假 LLM：``mode`` 决定回答形态（接地 / 空 / 只思考 / 直出 JSON）。

    grounded（默认）：取首块引用正文作答——回答内容由检索装配的上下文决定，
    链路退化会改变回答，从而被判分器捕获（而非硬编码文案自证）。
    """

    def __init__(self):
        self.mode = "grounded"
        self.reply = ""
        self.requests: list[Any] = []

    def _compose(self, messages: Any) -> str:
        user_prompt = ""
        for message in reversed(messages or []):
            if (message or {}).get("role") == "user":
                user_prompt = str((message or {}).get("content") or "")
                break
        self.requests.append(messages)
        if self.mode in ("empty", "reasoning_only"):
            return ""
        if self.mode == "reply":
            return self.reply
        block = _first_block(user_prompt)
        return f"根据 [1]：{block}" if block else ""

    def post(self, url: str, json: Any = None, headers: Any = None, timeout: Any = None) -> _FakeResponse:
        messages = (json or {}).get("messages") or []
        return _FakeResponse({"choices": [{"message": {"content": self._compose(messages)}}]})

    def chat_stream(self, messages: Any, **overrides: Any) -> Any:
        if self.mode == "reasoning_only":
            yield {"type": "reasoning", "text": "检索文档后仍在推演"}
            return
        answer = self._compose(messages)
        if answer:
            yield {"type": "content", "text": answer}

    async def achat_stream(self, messages: Any, **overrides: Any) -> Any:
        for item in self.chat_stream(messages, **overrides):
            yield item


# --------------------------------------------------------------------- 夹具


@pytest.fixture
def stub(monkeypatch):
    """安装确定性假 LLM（同步 chat / 同步流 / 异步流三条链路全覆盖）。"""
    fake = GroundedStubLLM()
    monkeypatch.setattr(
        "integrations.sdk.ai.chat.ChatCompletionsClient._request",
        lambda self, url, kwargs: fake.post(url, **kwargs),
    )
    monkeypatch.setattr(
        "integrations.sdk.ai.chat.ChatCompletionsClient.chat_stream",
        lambda self, messages, **kwargs: fake.chat_stream(messages, **kwargs),
    )
    monkeypatch.setattr(
        "integrations.sdk.ai.async_chat.AsyncChatCompletionsClient.chat_stream",
        lambda self, messages, **kwargs: fake.achat_stream(messages, **kwargs),
    )
    return fake


@pytest.fixture
def eval_env(settings, superuser):
    """评测环境：AI 开关 + 受控知识库语料 + NL 数据集与模型字段白名单。"""
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_BASE_URL = "https://ai.example.com/v1"
    settings.AI_API_KEY = "sk-test"
    settings.AI_MODEL = "test-model"
    settings.AI_NL_QUERY_ENABLED = True

    for index, (path, title, content) in enumerate(KNOWLEDGE_DOCS):
        AiKnowledgeChunk.objects.create(
            source_path=path,
            title=title,
            chunk_index=0,
            content=content,
            content_hash=f"{index:064d}",
        )

    from dataset.models import Dataset
    from system.models import ModelLabelField

    root, _ = ModelLabelField.objects.get_or_create(
        name="identity.userinfo",
        defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "用户"},
    )
    for name in ("username", "is_active"):
        ModelLabelField.objects.get_or_create(
            name=name,
            parent=root,
            defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": name},
        )
    dataset = Dataset.objects.create(
        name=DATASET_NAME,
        bound_model="identity.userinfo",
        columns=["username", "is_active"],
        visibility="shared",
        creator=superuser,
    )
    return {"dataset": dataset}


def _iter_stream(response: Any) -> Any:
    """流式响应的字节块（测试用同步收集；异步迭代器经 async_to_sync 收集）。"""
    content = response.streaming_content
    if getattr(response, "is_async", False):
        from asgiref.sync import async_to_sync

        async def gather():
            return [chunk async for chunk in content]

        return async_to_sync(gather)()
    return content


def _sse_frames(response: Any) -> list:
    """解析 SSE 响应为 ``[(event, payload), ...]``。"""
    frames = []
    buffer = b""
    for chunk in _iter_stream(response):
        buffer += chunk if isinstance(chunk, bytes) else str(chunk).encode()
        while b"\n\n" in buffer:
            raw, buffer = buffer.split(b"\n\n", 1)
            event, data = "message", ""
            for line in raw.decode().splitlines():
                if line.startswith("event:"):
                    event = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data = line[len("data:") :].strip()
            frames.append((event, json.loads(data) if data else {}))
    return frames


# --------------------------------------------------------------------- 数据自洽守护


def test_eval_data_is_self_consistent():
    """评测集与受控语料自洽：出处必须存在、必含短语须在目标文档内、黑名单短语须不在。

    把「内容变化 / 数据漂移」与「链路退化」分开：前者在这里直接失败并列出问题项，
    后者由下面的判分用例给出回答级证据。
    """
    corpus = {path: content for path, __title, content in KNOWLEDGE_DOCS}
    problems: list[str] = []
    for case in CASES:
        if case["kind"] not in ("docs", "docs_stream"):
            continue
        sources = case.get("expected_sources") or []
        if not sources:
            problems.append(f"{case['id']}: 缺少 expected_sources")
            continue
        joined = ""
        for path in sources:
            if path not in corpus:
                problems.append(f"{case['id']}: 期望出处不在受控语料：{path}")
            joined += corpus.get(path, "")
        for phrase in case.get("required_phrases") or []:
            if phrase not in joined:
                problems.append(f"{case['id']}: 必含短语不在目标文档内：{phrase}")
        for phrase in case.get("forbidden_phrases") or []:
            if phrase in joined:
                problems.append(f"{case['id']}: 黑名单短语出现在目标文档内：{phrase}")
    assert not problems, "\n".join(problems)


# --------------------------------------------------------------------- 判分用例


def _run_docs(case: dict, stub: GroundedStubLLM, auth_client: Any) -> None:
    stub.mode = "grounded"
    if case["kind"] == "docs":
        body = auth_client.post(f"{ASSISTANT_URL}/ask", {"question": case["question"]}, format="json").json()
        assert body["code"] == 1000, body
        answer = body["data"]["answer"]
        sources = body["data"].get("sources")
    else:
        frames = _sse_frames(
            auth_client.post(f"{ASSISTANT_URL}/ask/stream", {"question": case["question"]}, format="json")
        )
        events = [event for event, __ in frames]
        assert events[-1] == "done", frames
        answer = "".join(frame["delta"] for event, frame in frames if event == "delta")
        sources = frames[-1][1]["sources"]
    failures = judge_answer(case, answer, sources)
    assert not failures, f"[{case['id']}] {failures}\nanswer={answer!r}\nsources={sources}"


def _nl_reply(mode: str, dataset_pk: str) -> str:
    if mode == "aggregate":
        return json.dumps({"dataset": dataset_pk, "mode": "aggregate", "metric": "count"})
    return json.dumps({"dataset": dataset_pk, "mode": "rows", "filters": [], "limit": 50})


def _run_nl(case: dict, stub: GroundedStubLLM, auth_client: Any, dataset_pk: Any) -> None:
    stub.mode = "reply"
    stub.reply = _nl_reply(case["expected_mode"], str(dataset_pk))
    if case["kind"] == "nl":
        body = auth_client.post(INTERPRET_URL, {"question": case["question"]}, format="json").json()
        assert body["code"] == 1000, body
        data = body["data"]
    else:
        frames = _sse_frames(auth_client.post(f"{INTERPRET_URL}/stream", {"question": case["question"]}, format="json"))
        events = [event for event, __ in frames]
        assert events[-1] == "done", frames
        data = frames[-1][1]
    assert data["dataset_name"] == case["expected_dataset_name"], data
    assert data["mode"] == case["expected_mode"], data
    answer = (data.get("message") or {}).get("content") or ""
    failures = judge_answer({**case, "expected_sources": []}, answer, None)
    assert not failures, f"[{case['id']}] {failures}\nsummary={answer!r}"


def _run_fallback(case: dict, stub: GroundedStubLLM, auth_client: Any) -> None:
    stub.mode = {"empty_answer": "empty", "reasoning_only": "reasoning_only"}.get(case["scenario"], "grounded")
    if case["via"] == "ask":
        body = auth_client.post(f"{ASSISTANT_URL}/ask", {"question": case["question"]}, format="json").json()
        assert body["code"] == case["expected_code"], body
        assert body["detail"] == str(gettext(case["expected_detail"])), body
    else:
        frames = _sse_frames(
            auth_client.post(f"{ASSISTANT_URL}/ask/stream", {"question": case["question"]}, format="json")
        )
        events = [event for event, __ in frames]
        assert events[-1] == "error", frames
        assert frames[-1][1]["detail"] == str(gettext(case["expected_detail"])), frames[-1][1]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_answer_quality(case, eval_env, stub, auth_client):
    """逐条评测：按 kind 分发到对应链路，最终由 judge_answer 给出通过 / 失败证据。"""
    kind = case["kind"]
    if kind in ("docs", "docs_stream"):
        _run_docs(case, stub, auth_client)
    elif kind in ("nl", "nl_stream"):
        _run_nl(case, stub, auth_client, eval_env["dataset"].pk)
    elif kind == "fallback":
        _run_fallback(case, stub, auth_client)
    else:
        pytest.fail(f"未知用例类型：{kind}")
