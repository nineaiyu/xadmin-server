# -*- coding: utf-8 -*-
"""知识库向量构建与混合检索集成测试（假 embedding 客户端，不触外部服务）。

覆盖：
- 未配置 embedding 档案时向量链路整体不启用（词频检索零变化）；
- 显式构建（幂等补缺 / force 全量 / dry-run 不写库 / 维度不一致失败语义）；
- RRF 混合：仅向量召回的块进入结果，且语义最相近者排首位；
- 陈旧向量（正文变更后）被向量通道跳过（词频通道兜底）；
- 分块重建保留未变块的向量（重算成本只落在变更块）。
"""

import hashlib

import pytest

from ai.models.ai import AiKnowledgeChunk, AiProfile
from ai.utils.ai_embeddings import build_embeddings, invalidate_vector_index, vector_index, vector_stats
from ai.utils.ai_index import invalidate_chunk_index
from ai.utils.ai_retrieval import retrieve
from integrations.sdk.ai.chat import AiSdkError

pytestmark = pytest.mark.django_db

QUESTION = "数据库备份"


@pytest.fixture(autouse=True)
def _clear_caches():
    invalidate_chunk_index()
    invalidate_vector_index()
    yield
    invalidate_chunk_index()
    invalidate_vector_index()


def _make_chunk(path, index, content, title=""):
    return AiKnowledgeChunk.objects.create(
        source_path=path,
        chunk_index=index,
        content=content,
        title=title,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def _make_embedding_profile(model="fake-embed", active=True):
    profile = AiProfile.objects.create(
        name=f"embed-{AiProfile.objects.count() + 1}",
        base_url="http://ai.local/v1",
        model=model,
        purpose=AiProfile.Purpose.EMBEDDING,
        is_active=active,
    )
    profile.api_key_plain = "test-key"
    profile.save(update_fields=["api_key"])
    return profile


class _StubEmbeddingClient:
    """确定性假客户端：按文本映射返回向量（未命中用默认向量），可注入失败。"""

    mapping: dict = {}
    fail: bool = False
    model = "fake-embed"

    def __init__(self, credentials=None, http_client=None):
        self.model = str((credentials or {}).get("model") or type(self).model)
        self.last_usage = {"prompt_tokens": 1, "total_tokens": 1}

    def embed(self, texts):
        if type(self).fail:
            raise AiSdkError("provider down")
        default = type(self).mapping.get("__default__", [1.0, 0.0])
        return [list(type(self).mapping.get(text, default)) for text in texts]


@pytest.fixture
def stub_client(monkeypatch):
    def _install(mapping, fail=False):
        _StubEmbeddingClient.mapping = dict(mapping)
        _StubEmbeddingClient.fail = fail
        monkeypatch.setattr("integrations.sdk.ai.embeddings.EmbeddingClient", _StubEmbeddingClient)
        return _StubEmbeddingClient

    yield _install
    _StubEmbeddingClient.mapping = {}
    _StubEmbeddingClient.fail = False


class TestDisabledWithoutProfile:
    def test_retrieve_matches_token_path_when_no_profile(self, monkeypatch):
        """未配置 embedding 档案：向量链路不参与，检索与历史实现等价。"""
        chunk = _make_chunk("docs/a.md", 0, "数据库备份与恢复演练说明", title="备份")
        assert vector_index() is None
        assert [item["chunk"].pk for item in retrieve(QUESTION)] == [chunk.pk]
        from ai.utils.ai_index import _tokenize
        from ai.utils.ai_retrieval import _retrieve_full_scan

        full = _retrieve_full_scan(set(_tokenize(QUESTION)), 5)
        assert [item["chunk"].pk for item in retrieve(QUESTION)] == [item["chunk"].pk for item in full]

    def test_build_reports_disabled(self):
        _make_chunk("docs/a.md", 0, "数据库备份说明")
        summary = build_embeddings()
        assert summary["enabled"] is False
        assert summary["embedded"] == 0
        assert vector_stats()["enabled"] is False


class TestBuildAndHybrid:
    def test_build_writes_vectors_and_stats(self, stub_client, monkeypatch):
        monkeypatch.setattr("ai.utils.ai_embeddings.MIN_INDEXED_VECTORS", 1)
        _make_embedding_profile()
        content = "数据库备份与恢复演练说明"
        stub_client({content: [0.5, 0.5], QUESTION: [1.0, 0.0]})
        chunk = _make_chunk("docs/a.md", 0, content)

        summary = build_embeddings()
        assert summary["enabled"] is True and summary["ok"] is True
        assert summary["embedded"] == 1 and summary["skipped"] == 0 and summary["dim"] == 2
        chunk.refresh_from_db()
        assert chunk.embedding_model == "fake-embed" and chunk.embedding_dim == 2
        assert chunk.embedding_hash == chunk.content_hash

        stats = vector_stats()
        assert stats["enabled"] is True and stats["fresh"] == 1 and stats["stale"] == 0
        assert stats["dim"] == 2 and stats["total"] == 1

        # 幂等：重复构建不再调用供应商（embedded=0 / skipped=1）
        again = build_embeddings()
        assert again["embedded"] == 0 and again["skipped"] == 1

    def test_force_rebuild_recomputes(self, stub_client, monkeypatch):
        monkeypatch.setattr("ai.utils.ai_embeddings.MIN_INDEXED_VECTORS", 1)
        _make_embedding_profile()
        content = "数据库备份与恢复演练说明"
        stub_client({content: [0.5, 0.5]})
        _make_chunk("docs/a.md", 0, content)
        assert build_embeddings()["embedded"] == 1
        forced = build_embeddings(force=True)
        assert forced["embedded"] == 1 and forced["skipped"] == 0

    def test_dry_run_does_not_write(self, stub_client):
        _make_embedding_profile()
        stub_client({"数据库备份说明": [0.5, 0.5]})
        chunk = _make_chunk("docs/a.md", 0, "数据库备份说明")
        summary = build_embeddings(dry_run=True)
        assert summary["enabled"] is True and summary["embedded"] == 0
        chunk.refresh_from_db()
        assert chunk.embedding is None

    def test_hybrid_recalls_semantic_only_chunk(self, stub_client, monkeypatch):
        """语义相近但无词元重叠的块应被向量通道召回（单通道命中不排首位=RRF 设计）。"""
        monkeypatch.setattr("ai.utils.ai_embeddings.MIN_INDEXED_VECTORS", 1)
        _make_embedding_profile()
        lexical_a = _make_chunk("docs/lex.md", 0, "数据库备份与恢复演练说明", title="备份")
        lexical_b = _make_chunk("docs/lex.md", 1, "数据库备份策略说明")
        semantic = _make_chunk("docs/sem.md", 0, "项目排期与里程碑规划", title="排期")
        stub_client(
            {
                QUESTION: [1.0, 0.0],
                lexical_a.content: [0.2, 0.98],
                lexical_b.content: [0.1, 0.99],
                semantic.content: [1.0, 0.0],
                "__default__": [0.0, 1.0],
            }
        )
        build_embeddings()

        results = retrieve(QUESTION)
        pks = [item["chunk"].pk for item in results]
        assert semantic.pk in pks, "向量通道召回的块必须进入结果"
        assert set(pks) & {lexical_a.pk, lexical_b.pk} == {lexical_a.pk, lexical_b.pk}, "词频通道命中不丢"
        # 双通道共识（lexical_a 词频第 1 + 向量第 2）高于单通道命中：RRF 的标准行为
        assert pks[0] == lexical_a.pk

    def test_vector_channel_survives_provider_failure(self, stub_client, monkeypatch):
        """查询向量化失败时静默回退词频结果（问答可用性优先）。"""
        monkeypatch.setattr("ai.utils.ai_embeddings.MIN_INDEXED_VECTORS", 1)
        _make_embedding_profile()
        content = "数据库备份与恢复演练说明"
        stub_client({content: [0.5, 0.5], QUESTION: [1.0, 0.0]})
        chunk = _make_chunk("docs/a.md", 0, content)
        build_embeddings()

        _StubEmbeddingClient.fail = True
        invalidate_vector_index()
        assert [item["chunk"].pk for item in retrieve(QUESTION)] == [chunk.pk]

    def test_failure_reports_partial_progress(self, stub_client):
        _make_embedding_profile()
        stub_client({}, fail=True)
        _make_chunk("docs/a.md", 0, "数据库备份说明")
        summary = build_embeddings()
        assert summary["enabled"] is True and summary["ok"] is False
        assert summary["failed"] == 1 and summary["embedded"] == 0

    def test_dimension_mismatch_fails(self, stub_client):
        _make_embedding_profile()
        stub_client(
            {
                "数据库备份说明": [0.5, 0.5],
                "审批流程说明": [0.5, 0.5, 0.5],
            }
        )
        _make_chunk("docs/a.md", 0, "数据库备份说明")
        _make_chunk("docs/b.md", 0, "审批流程说明")
        summary = build_embeddings()
        assert summary["ok"] is False
        assert summary["detail"] == "inconsistent embedding dimension"


class TestStaleVectors:
    def test_stale_vector_skipped_by_vector_channel(self, stub_client, monkeypatch):
        monkeypatch.setattr("ai.utils.ai_embeddings.MIN_INDEXED_VECTORS", 1)
        _make_embedding_profile()
        chunk = _make_chunk("docs/a.md", 0, "数据库备份与恢复演练说明")
        stub_client({"数据库备份与恢复演练说明": [1.0, 0.0], QUESTION: [1.0, 0.0]})
        build_embeddings()
        assert vector_index() is not None

        chunk.content = "全新的内容"
        chunk.content_hash = hashlib.sha256("全新的内容".encode()).hexdigest()
        chunk.save(update_fields=["content", "content_hash"])
        invalidate_vector_index()

        assert vector_index() is None, "正文变更后的向量不得参与检索"
        stats = vector_stats()
        assert stats["stale"] == 1 and stats["fresh"] == 0
        # 词频通道仍可命中新内容
        assert chunk.pk in {item["chunk"].pk for item in retrieve("全新内容")}

    def test_model_switch_ignores_old_vectors(self, stub_client, monkeypatch):
        monkeypatch.setattr("ai.utils.ai_embeddings.MIN_INDEXED_VECTORS", 1)
        profile = _make_embedding_profile(model="model-a")
        content = "数据库备份与恢复演练说明"
        stub_client({content: [1.0, 0.0]})
        _make_chunk("docs/a.md", 0, content)
        build_embeddings()
        invalidate_vector_index()
        assert vector_index() is not None

        profile.model = "model-b"
        profile.save(update_fields=["model"])
        invalidate_vector_index()
        assert vector_index() is None, "切换 embedding 模型后旧向量不参与检索"


class TestRebuildPreservesVectors:
    def test_rebuild_keeps_unchanged_chunk_vectors(self, stub_client, monkeypatch):
        monkeypatch.setattr("ai.utils.ai_embeddings.MIN_INDEXED_VECTORS", 1)
        _make_embedding_profile()
        from ai.models.ai import AiKnowledgeDocument
        from ai.utils.ai import rebuild_chunks

        content = "## 备份\n数据库备份与恢复演练说明\n\n## 审批\n审批流程会签规则说明"
        stub_client(
            {
                "## 备份\n数据库备份与恢复演练说明": [1.0, 0.0],
                "## 审批\n审批流程会签规则说明": [0.0, 1.0],
            }
        )
        doc = AiKnowledgeDocument.objects.create(
            path="docs/keep.md",
            title="keep",
            content=content,
            content_hash=AiKnowledgeDocument.hash_content(content),
        )
        assert rebuild_chunks(doc) == 2
        assert build_embeddings()["embedded"] == 2

        # 重建（内容不变）：向量按 content_hash 保留，无需重算
        assert rebuild_chunks(doc) == 2
        rows = list(AiKnowledgeChunk.objects.filter(source_path="docs/keep.md").values_list("embedding", flat=True))
        assert all(row is not None for row in rows)
        assert build_embeddings()["embedded"] == 0

        # 变更一块：仅该块失去向量（陈旧），另一块保留
        doc.content = content.replace("审批流程会签规则说明", "审批流程或签规则说明")
        doc.save(update_fields=["content"])
        assert rebuild_chunks(doc) == 2
        fresh = AiKnowledgeChunk.objects.filter(source_path="docs/keep.md", embedding_hash="").count()
        assert fresh == 1


# ---------------------------------------------------------------- 7.3 异步构建与进度通道

KNOWLEDGE_URL = "/api/ai/knowledge-documents"


class TestAsyncBuildTask:
    """异步构建任务：单飞锁 / 进度上报 / 终态摘要 / 端点契约。"""

    def test_task_runs_and_writes_terminal_status(self, stub_client):
        stub_client({"__default__": [1.0, 0.0]})
        _make_embedding_profile()
        _make_chunk("docs/a.md", 0, "数据库备份说明")

        from ai.tasks import build_embeddings_task

        result = build_embeddings_task.apply(args=["", False])
        assert result.successful()
        assert result.get()["embedded"] == 1

        from ai.utils.embedding_progress import get_status

        status = get_status()
        assert status["state"] == "done"
        assert status["percent"] == 100
        assert status["summary"]["embedded"] == 1
        # 任务结束释放单飞锁（可再次提交）
        from ai.utils.embedding_progress import try_acquire_lock

        assert try_acquire_lock() is True

    def test_progress_callback_reports_batches(self, stub_client):
        stub_client({"__default__": [1.0, 0.0]})
        _make_embedding_profile()
        for index in range(3):
            _make_chunk("docs/a.md", index, f"数据库备份说明 {index}")
        seen = []

        from ai.utils.ai_embeddings import build_embeddings

        build_embeddings(
            batch_size=2, progress_cb=lambda percent, stage="", embedded=0: seen.append((percent, embedded))
        )
        assert seen[0][0] == 0  # 开工即上报
        assert seen[-1][1] == 3  # 终点已构建数 = 总数
        assert seen[-1][0] == 100

    def test_single_flight_lock_rejects_second_submit(self, auth_client, stub_client, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = False  # 不真跑任务，只验证锁语义
        stub_client({"__default__": [1.0, 0.0]})
        _make_embedding_profile()

        from ai.utils.embedding_progress import release_lock

        release_lock()
        first = auth_client.post(f"{KNOWLEDGE_URL}/build-embeddings", {}, format="json")
        assert first.status_code == 200, first.data
        second = auth_client.post(f"{KNOWLEDGE_URL}/build-embeddings", {}, format="json")
        assert second.json()["code"] == 1001
        release_lock()

    def test_endpoint_requires_embedding_profile(self, auth_client):
        from ai.utils.embedding_progress import release_lock

        release_lock()
        response = auth_client.post(f"{KNOWLEDGE_URL}/build-embeddings", {}, format="json")
        assert response.json()["code"] == 1001
        assert "profile" in response.json()["detail"] or "配置" in response.json()["detail"]

    def test_status_endpoint_idle_by_default(self, auth_client):
        from django.core.cache import cache

        cache.delete("ai_embedding_build_status")
        response = auth_client.get(f"{KNOWLEDGE_URL}/build-embeddings/status")
        assert response.status_code == 200
        assert response.json()["data"]["state"] in ("idle", "done", "error", "running")

    def test_eager_submit_writes_terminal_summary(self, auth_client, stub_client, settings):
        """eager（测试）形态：提交即同步跑完，状态落终态（与导出任务同口径）。"""
        settings.CELERY_TASK_ALWAYS_EAGER = True
        stub_client({"__default__": [1.0, 0.0]})
        _make_embedding_profile()
        _make_chunk("docs/a.md", 0, "数据库备份说明")

        from ai.utils.embedding_progress import release_lock

        release_lock()
        response = auth_client.post(f"{KNOWLEDGE_URL}/build-embeddings", {}, format="json")
        assert response.status_code == 200, response.data
        status = auth_client.get(f"{KNOWLEDGE_URL}/build-embeddings/status").json()["data"]
        assert status["state"] == "done"
        assert status["summary"]["embedded"] == 1

    def test_first_poll_after_submit_never_hits_previous_terminal_state(self, auth_client, stub_client, settings):
        """提交即翻 running：上一轮随通道保留的旧终态从提交时刻起不可命中，
        首轮轮询（任务尚未被 worker 拉起）读到的只能是 running，而非旧摘要。"""
        stub_client({"__default__": [1.0, 0.0]})
        _make_embedding_profile()

        from django.core.cache import cache

        from ai.utils.embedding_progress import BUILD_STATUS_KEY, release_lock

        cache.set(
            BUILD_STATUS_KEY,
            {"state": "done", "summary": {"embedded": 9, "skipped": 1}, "finished_time": "prev"},
            3600,
        )
        settings.CELERY_TASK_ALWAYS_EAGER = False  # 只验证提交时刻的通道语义，不真跑任务
        response = auth_client.post(f"{KNOWLEDGE_URL}/build-embeddings", {}, format="json")
        assert response.status_code == 200, response.data
        status = auth_client.get(f"{KNOWLEDGE_URL}/build-embeddings/status").json()["data"]
        assert status["state"] == "running"
        assert "summary" not in status
        assert "finished_time" not in status
        release_lock()
