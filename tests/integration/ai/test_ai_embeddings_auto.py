# -*- coding: utf-8 -*-
"""向量自动重算守护。

正文变更后自动增量补齐向量：调度入口三重前置（embedding 档案 / 待建块 / 构建单飞锁），
与手工构建共用同一条状态机（测试档 celery eager，调度即同步完成构建）。
覆盖：上传即建 / 正文变更补陈旧 / 无档案零触发 / 锁被占跳过不排队 / 未变重传不调度 /
仓库同步一次全量增量调度 / 模型换档只补变更文档（不自动全量重嵌）。

密闭性：单飞锁原语（try_acquire_lock / release_lock）打桩——真实锁落共享 Redis，
其他 xdist worker 构建任务的 finally 释放会与本用例的「持锁」假设竞态（全量轮实测
踩中）。打桩后用例只验证调度入口的判定与分支，锁本体语义由手工构建链路既有用例覆盖。
"""

import hashlib
from types import SimpleNamespace

import pytest

from ai.models.ai import AiKnowledgeChunk, AiProfile
from ai.utils.ai_embeddings import invalidate_vector_index, schedule_auto_rebuild, vector_stats
from ai.utils.ai_index import invalidate_chunk_index

pytestmark = pytest.mark.django_db

CONTENT_V1 = "数据库备份与恢复演练说明"
CONTENT_V2 = "数据库备份与恢复演练说明（2026 修订版）"
QUESTION = "数据库备份"


@pytest.fixture(autouse=True)
def _clear_caches():
    invalidate_chunk_index()
    invalidate_vector_index()
    yield
    invalidate_chunk_index()
    invalidate_vector_index()


@pytest.fixture(autouse=True)
def _hermetic_lock(monkeypatch):
    """锁原语打桩：默认「锁空闲」（调度可继续），释放为 no-op（不触共享 Redis）。"""
    monkeypatch.setattr("ai.utils.embedding_progress.try_acquire_lock", lambda: True)
    monkeypatch.setattr("ai.utils.embedding_progress.release_lock", lambda: None)


def _make_embedding_profile(model="fake-embed"):
    profile = AiProfile.objects.create(
        name=f"embed-{AiProfile.objects.count() + 1}",
        base_url="http://ai.local/v1",
        model=model,
        purpose=AiProfile.Purpose.EMBEDDING,
        is_active=True,
    )
    profile.api_key_plain = "test-key"
    profile.save(update_fields=["api_key"])
    return profile


class _StubEmbeddingClient:
    """确定性假客户端（与 test_ai_embeddings_pipeline 同思路）：按文本映射返回向量。"""

    mapping: dict = {}
    fail: bool = False
    model = "fake-embed"

    def __init__(self, credentials=None, http_client=None):
        self.model = str((credentials or {}).get("model") or type(self).model)
        self.last_usage = {"prompt_tokens": 1, "total_tokens": 1}

    def embed(self, texts):
        if type(self).fail:
            from integrations.sdk.ai.chat import AiSdkError

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


def _patch_task_spy(monkeypatch):
    """把构建任务换成探针（只记录 apply 调用，不真正构建）。"""
    applied: list = []
    monkeypatch.setattr("ai.tasks.build_embeddings_task", SimpleNamespace(apply=lambda args=None: applied.append(args)))
    return applied


def _chunk_of(doc):
    return AiKnowledgeChunk.objects.get(source_path=doc.path)


def _upload(name: str, content: str):
    from ai.utils.ai_knowledge import upsert_upload_document

    return upsert_upload_document(name, content)


def _sync() -> dict:
    from ai.utils.ai_knowledge import sync_knowledge

    return sync_knowledge()


class TestAutoRebuildOnUpload:
    def test_upload_builds_vectors_automatically(self, stub_client):
        """有档案 + 上传新文档：调度即建，块直接新鲜（eager 同步完成）。"""
        profile = _make_embedding_profile()
        stub_client({CONTENT_V1: [0.5, 0.5], QUESTION: [1.0, 0.0]})
        doc, created = _upload("backup.md", CONTENT_V1)
        assert created is True
        chunk = _chunk_of(doc)
        assert chunk.embedding is not None
        assert chunk.embedding_hash == chunk.content_hash
        assert chunk.embedding_model == profile.model
        stats = vector_stats()
        assert stats["fresh"] == 1 and stats["stale"] == 0

    def test_content_update_rebuilds_stale(self, stub_client):
        """正文变更（同名覆盖）：陈旧块自动补齐，重传后无残留陈旧。"""
        _make_embedding_profile()
        stub_client({"__default__": [0.5, 0.5], QUESTION: [1.0, 0.0]})
        _upload("backup.md", CONTENT_V1)
        doc, created = _upload("backup.md", CONTENT_V2)
        assert created is False
        chunk = _chunk_of(doc)
        assert chunk.content_hash == hashlib.sha256(CONTENT_V2.encode("utf-8")).hexdigest()
        assert chunk.embedding_hash == chunk.content_hash
        assert vector_stats()["stale"] == 0

    def test_no_profile_is_noop(self):
        """无 embedding 档案：零调度零异常，块保持未向量化（部署行为零变化）。"""
        doc, _created = _upload("backup.md", CONTENT_V1)
        assert _chunk_of(doc).embedding is None
        assert schedule_auto_rebuild(doc) is False
        assert vector_stats()["enabled"] is False

    def test_lock_held_skips_without_queueing(self, stub_client, monkeypatch):
        """构建单飞锁被占：跳过且不排队（不重复消耗供应商预算）。"""
        _make_embedding_profile()
        stub_client({"__default__": [0.5, 0.5]})
        applied = _patch_task_spy(monkeypatch)
        # 覆写夹具默认桩：锁被占（try_acquire_lock → False）
        monkeypatch.setattr("ai.utils.embedding_progress.try_acquire_lock", lambda: False)
        doc, _created = _upload("backup.md", CONTENT_V1)
        assert _chunk_of(doc).embedding is None
        assert applied == []
        assert schedule_auto_rebuild(doc) is False

    def test_unchanged_reupload_skips_schedule(self, stub_client, monkeypatch):
        """未变内容重传：向量被 rebuild 保留 → 无待建块 → 不调度（不空转占锁）。"""
        _make_embedding_profile()
        stub_client({CONTENT_V1: [0.5, 0.5], QUESTION: [1.0, 0.0]})
        _upload("backup.md", CONTENT_V1)
        applied = _patch_task_spy(monkeypatch)
        doc, created = _upload("backup.md", CONTENT_V1)
        assert created is False
        assert applied == []
        assert _chunk_of(doc).embedding is not None
        assert schedule_auto_rebuild(doc) is False


class TestAutoRebuildOnSync:
    def test_sync_schedules_single_full_rebuild(self, stub_client, monkeypatch, tmp_path):
        """仓库同步变更：只调度一次全量增量构建（一次任务吃掉全部陈旧块）。"""
        _make_embedding_profile()
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "a.md").write_text(f"# A\n\n{CONTENT_V1}", encoding="utf-8")
        (docs / "b.md").write_text(f"# B\n\n{CONTENT_V2}", encoding="utf-8")
        stub_client({"__default__": [0.3, 0.9], QUESTION: [1.0, 0.0]})
        monkeypatch.setattr("ai.utils.ai_knowledge.DOCS_DIR", docs)
        monkeypatch.setattr("ai.utils.ai_knowledge.ROOT_DOCS", [])
        summary = _sync()
        assert summary["created"] == 2
        assert summary["vector_rebuild"] is True
        assert AiKnowledgeChunk.objects.exclude(embedding__isnull=True).count() == 2

    def test_sync_without_changes_skips(self, stub_client, monkeypatch, tmp_path):
        """仓库无变更（hash 快路径）：不调度。"""
        _make_embedding_profile()
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "a.md").write_text(f"# A\n\n{CONTENT_V1}", encoding="utf-8")
        stub_client({"__default__": [0.3, 0.9]})
        monkeypatch.setattr("ai.utils.ai_knowledge.DOCS_DIR", docs)
        monkeypatch.setattr("ai.utils.ai_knowledge.ROOT_DOCS", [])
        assert _sync()["vector_rebuild"] is True
        assert _sync()["vector_rebuild"] is False


class TestModelSwitchBoundary:
    def test_model_switch_rebuilds_only_changed_doc(self, stub_client):
        """模型换档：变更文档按新模型补齐；未变更文档不自动重嵌（走构建按钮 force）。"""
        profile = _make_embedding_profile(model="fake-embed")
        stub_client({"__default__": [0.5, 0.5], QUESTION: [1.0, 0.0]})
        _upload("a.md", CONTENT_V1)
        _upload("b.md", CONTENT_V2)
        assert vector_stats()["stale"] == 0

        profile.model = "other-embed"
        profile.save(update_fields=["model"])
        _upload("a.md", CONTENT_V1 + " 附录")  # 文档 a 正文变更

        stats = vector_stats()
        assert stats["model"] == "other-embed"
        assert stats["fresh"] == 1  # 变更文档已按新模型补齐
        assert stats["stale"] == 1  # 未变更文档保持旧模型（向量通道跳过，词频兜底）
