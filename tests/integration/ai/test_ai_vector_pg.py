#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""pgvector 通道专属守护（落地）：SQL 检索语义与 HNSW 索引定型。

内存索引退役后的等价性口径：
- 新鲜/模型/维度三重过滤在 SQL WHERE 内收敛（维度不一致的行直接排除，等价旧
  实现的逐块 skip，且不触发 pgvector 维度错误）；
- 构建双写（embedding 二进制 + embedding_vector）；
- HNSW 索引定型（ai_vector_ddl）：维度稳定 → 定型列 + 建索引；维度混存 → 反向
  退回无维度；小语料 no-op；幂等可反复执行。
"""

import hashlib

import pytest
from django.db import connection

from ai.models.ai import AiKnowledgeChunk, AiProfile
from ai.utils import ai_vector_ddl
from ai.utils.ai_embedding_math import encode_vector
from ai.utils.ai_embeddings import build_embeddings, invalidate_vector_index, search_vectors, vector_index

pytestmark = pytest.mark.django_db


def _make_chunk(path, index, content):
    return AiKnowledgeChunk.objects.create(
        source_path=path,
        chunk_index=index,
        content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


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


def _store_vector(chunk, vector, model="fake-embed"):
    """直写双列（绕过供应商客户端）：hash/模型/维度按新鲜口径落库。"""
    chunk.embedding = encode_vector(vector)
    chunk.embedding_vector = vector
    chunk.embedding_hash = chunk.content_hash
    chunk.embedding_model = model
    chunk.embedding_dim = len(vector)
    chunk.save(update_fields=["embedding", "embedding_vector", "embedding_hash", "embedding_model", "embedding_dim"])


@pytest.fixture(autouse=True)
def _clear_caches():
    invalidate_vector_index()
    yield
    invalidate_vector_index()


@pytest.fixture(autouse=True)
def _stub_query_embed(monkeypatch):
    """查询向量打桩（检索链路的真实供应商调用在单测里不可达）：固定二维查询向量，
    与各用例写入的二维块向量同维；构建用例自带 monkeypatch 会覆盖本桩。"""
    monkeypatch.setattr(
        "integrations.sdk.ai.embeddings.EmbeddingClient.embed", lambda self, texts: [[1.0, 0.0] for _ in texts]
    )


class TestSearchVectorsSQL:
    def test_requires_min_fresh_vectors(self, db):
        _make_embedding_profile()
        for index in range(3):  # 低于 MIN_INDEXED_VECTORS=5
            chunk = _make_chunk("docs/a.md", index, f"内容{index}")
            _store_vector(chunk, [1.0, 0.0])
        assert vector_index() is None
        assert search_vectors("任意问题") == []

    def test_dim_mismatch_rows_excluded_without_error(self, db):
        """维度不一致的行必须被 SQL 过滤排除（等价旧实现 skip，且不得触发 pgvector 报错）。"""
        _make_embedding_profile()
        for index in range(6):
            chunk = _make_chunk("docs/a.md", index, f"内容{index}")
            _store_vector(chunk, [1.0, 0.0])
        odd = _make_chunk("docs/b.md", 0, "三维向量块")
        _store_vector(odd, [1.0, 0.0, 0.0])  # 维度 3，与查询维度 2 不一致
        ranked = search_vectors("内容")
        assert len(ranked) == 6
        assert odd.pk not in ranked

    def test_stale_and_model_mismatch_excluded(self, db):
        _make_embedding_profile()
        for index in range(6):
            chunk = _make_chunk("docs/a.md", index, f"内容{index}")
            _store_vector(chunk, [1.0, 0.0])
        stale = _make_chunk("docs/stale.md", 0, "旧正文")
        _store_vector(stale, [0.0, 1.0])
        AiKnowledgeChunk.objects.filter(pk=stale.pk).update(content_hash="x" * 64)  # 正文已变 → 陈旧
        other_model = _make_chunk("docs/other.md", 0, "别的模型")
        _store_vector(other_model, [0.0, 1.0], model="another-embed")
        ranked = search_vectors("内容")
        assert len(ranked) == 6
        assert stale.pk not in ranked and other_model.pk not in ranked

    def test_ordering_is_cosine_desc(self, db):
        _make_embedding_profile()
        near = _make_chunk("docs/near.md", 0, "近")
        _store_vector(near, [1.0, 0.0])
        far = _make_chunk("docs/far.md", 1, "远")
        _store_vector(far, [-1.0, 0.0])
        for index in range(2, 6):  # 凑够最小可用条数
            chunk = _make_chunk("docs/pad.md", index, f"垫{index}")
            _store_vector(chunk, [0.7, 0.7])
        ranked = search_vectors("近")
        assert ranked[0] == near.pk
        assert ranked[-1] == far.pk  # 余弦距离最大（2.0），排末位（top_k 默认 50 覆盖全部候选）


class TestBuildDualWrite:
    def test_build_writes_both_columns(self, db, monkeypatch):
        _make_embedding_profile()
        chunk = _make_chunk("docs/a.md", 0, "正文")
        monkeypatch.setattr(
            "integrations.sdk.ai.embeddings.EmbeddingClient.embed", lambda self, texts: [[1.0, 0.0] for _ in texts]
        )
        summary = build_embeddings()
        assert summary["ok"] and summary["embedded"] == 1
        chunk.refresh_from_db()
        assert chunk.embedding is not None, "迁移窗口内二进制列必须双写保留（回滚=代码回退）"
        assert list(chunk.embedding_vector) == [1.0, 0.0]


@pytest.mark.django_db(transaction=True)
class TestEnsureVectorIndex:
    """DDL 用例走事务型库：ALTER TYPE 在测试事务内会撞 pending trigger events
    （生产路径为 celery/命令的 autocommit，无此约束）。"""

    @pytest.fixture(autouse=True)
    def _small_threshold(self, monkeypatch):
        monkeypatch.setattr(ai_vector_ddl, "INDEX_MIN_ROWS", 2)

    def _rows(self, vectors):
        for index, vector in enumerate(vectors):
            chunk = _make_chunk("docs/idx.md", index, f"块{index}")
            _store_vector(chunk, vector)

    def test_noop_without_vectors(self, db):
        assert ai_vector_ddl.ensure_vector_index()["action"] == ai_vector_ddl.ACTION_SKIPPED

    def test_types_column_and_builds_hnsw(self, db):
        _make_embedding_profile()
        self._rows([[1.0, 0.0]] * 6)  # ≥ MIN_INDEXED_VECTORS，检索门槛与 DDL 门槛（已调小）分开
        result = ai_vector_ddl.ensure_vector_index()
        assert result["action"] == ai_vector_ddl.ACTION_INDEXED
        state = ai_vector_ddl.vector_index_state()
        assert state["column_type"] == "vector(2)"
        assert state["hnsw"]
        # 幂等：再跑一次不升级动作
        assert ai_vector_ddl.ensure_vector_index()["action"] == ai_vector_ddl.ACTION_NOOP
        # 定型后检索照常（SQL 余弦带索引）
        assert len(search_vectors("块")) == 6

    def test_mixed_dims_revert_to_untyped(self, db):
        """生产换档顺序：列已定型 → 新维度写入前构建路径先调 ensure_column_accepts_dim
        反向定型 → 新维度向量照常写入 → ensure_vector_index 判定维度混存维持无维度。"""
        self._rows([[1.0, 0.0]] * 2)
        ai_vector_ddl.ensure_vector_index()
        result = ai_vector_ddl.ensure_column_accepts_dim(3)  # 模型换档后的新维度
        assert result["action"] == ai_vector_ddl.ACTION_UNTYPED
        assert ai_vector_ddl.vector_index_state()["column_type"] == "vector"
        odd = _make_chunk("docs/mixed.md", 99, "换档向量")
        _store_vector(odd, [1.0, 0.0, 0.0])  # 反向定型后新维度可写
        assert ai_vector_ddl.ensure_vector_index()["action"] == ai_vector_ddl.ACTION_UNTYPED
        state = ai_vector_ddl.vector_index_state()
        assert state["column_type"] == "vector"
        assert not state["hnsw"]
        # 反向定型后写入任意维度不受影响
        another = _make_chunk("docs/mixed2.md", 100, "再写入")
        _store_vector(another, [0.5, 0.5, 0.5, 0.5])
        another.refresh_from_db()
        assert list(another.embedding_vector) == [0.5, 0.5, 0.5, 0.5]

    def test_vendor_guard(self, db, monkeypatch):
        monkeypatch.setattr(connection, "vendor", "mysql")
        assert ai_vector_ddl.ensure_vector_index()["action"] == ai_vector_ddl.ACTION_SKIPPED
