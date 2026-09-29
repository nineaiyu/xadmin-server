# -*- coding: utf-8 -*-
"""知识库块级分词缓存：结果一致性（与全扫逐块分词一致）+ 增量维护 + 容量退避。

断言只针对本用例创建的块（`pk in 结果集`）——同进程其它测试可能已写入知识块
（如评测集测试同步仓库文档），不依赖「空库」前提。
"""

import pytest

from ai.models.ai import AiKnowledgeChunk
from ai.utils.ai_retrieval import retrieve

pytestmark = pytest.mark.django_db

CONTENT_A = "数据库备份与恢复演练说明"
CONTENT_B = "审批流程引擎的会签与或签规则说明"


def make_chunk(path, index, content, title=""):
    return AiKnowledgeChunk.objects.create(
        source_path=path, chunk_index=index, content=content, title=title, content_hash=f"h-{path}-{index}"
    )


def hit_pks(question) -> set:
    return {item["chunk"].pk for item in retrieve(question)}


@pytest.fixture(autouse=True)
def _clear_index():
    from ai.utils.ai_index import invalidate_chunk_index

    invalidate_chunk_index()
    yield
    invalidate_chunk_index()


class TestRetrievalIndexParity:
    def test_index_path_matches_full_scan(self):
        make_chunk("docs/a.md", 0, CONTENT_A, title="备份")
        make_chunk("docs/b.md", 1, CONTENT_B, title="审批")
        from ai.utils.ai_index import _tokenize
        from ai.utils.ai_retrieval import _retrieve_full_scan

        question = "数据库备份"
        indexed = retrieve(question)
        full = _retrieve_full_scan(set(_tokenize(question)), 5)
        assert indexed, "索引路径应有命中"
        assert [(item["chunk"].pk, item["score"]) for item in indexed] == [
            (item["chunk"].pk, item["score"]) for item in full
        ]

    def test_title_bonus_keeps_borderline_chunk(self):
        """标题加成计入达标判定：内容仅命中 1 个词元 + 标题命中 → 达标入选。"""
        borderline = make_chunk("docs/t.md", 0, "库备说明", title="备份")
        full_hit = make_chunk("docs/t.md", 1, CONTENT_A, title="")

        hits = hit_pks("数据库备份")
        assert {borderline.pk, full_hit.pk} <= hits

    def test_content_change_invalidates_entry(self):
        from ai.utils.ai_index import invalidate_chunk_index

        chunk = make_chunk("docs/c.md", 0, CONTENT_A)
        assert chunk.pk in hit_pks("数据库备份")

        chunk.content = CONTENT_B
        chunk.content_hash = "changed-hash"
        chunk.save(update_fields=["content", "content_hash"])
        invalidate_chunk_index()  # 真实写入路径（rebuild_chunks）的显式失效

        assert chunk.pk in hit_pks("会签规则"), "内容变更后新问题应命中"
        assert chunk.pk not in hit_pks("数据库备份"), "内容变更后旧问题不应命中"

    def test_deleted_chunk_removed(self):
        from ai.utils.ai_index import invalidate_chunk_index

        chunk = make_chunk("docs/d.md", 0, CONTENT_A)
        assert chunk.pk in hit_pks("数据库备份")
        chunk.delete()
        invalidate_chunk_index()  # 真实写入路径（remove_chunks）的显式失效
        assert chunk.pk not in hit_pks("数据库备份")

    def test_new_chunk_picked_up(self):
        from ai.utils.ai_index import invalidate_chunk_index

        make_chunk("docs/other.md", 0, CONTENT_B)  # 先预热元数据缓存
        assert hit_pks("会签规则")
        chunk = make_chunk("docs/new.md", 0, CONTENT_A)
        invalidate_chunk_index()  # 真实写入路径（rebuild_chunks）的显式失效
        assert chunk.pk in hit_pks("数据库备份"), "新增块应被增量索引纳管"

    def test_capacity_overflow_falls_back(self, monkeypatch):
        chunk = make_chunk("docs/e.md", 0, CONTENT_A)
        from ai.utils import ai_index

        monkeypatch.setattr(ai_index, "MAX_INDEXED_CHUNKS", 0)
        assert ai_index.chunk_token_index() is None
        assert chunk.pk in hit_pks("数据库备份"), "退避路径（逐块分词全扫）仍可检索"


class TestMetaRowsShortCache:
    """索引元数据签名短缓存：窗口内复用、写入路径显式失效、TTL 过期自愈。"""

    @staticmethod
    def _meta_queries(ctx):
        """元数据签名查询（values_list 带 content_hash 列）；命中回读不含该列。"""
        return [
            q["sql"]
            for q in ctx.captured_queries
            if "system_aiknowledgechunk" in q["sql"] and "content_hash" in q["sql"]
        ]

    def test_repeated_retrieval_reuses_meta_rows(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from ai.utils.ai_index import invalidate_chunk_index

        make_chunk("docs/meta.md", 0, CONTENT_A)
        invalidate_chunk_index()
        with CaptureQueriesContext(connection) as first:
            assert hit_pks("数据库备份")
        with CaptureQueriesContext(connection) as second:
            assert hit_pks("数据库备份")
        assert self._meta_queries(first), "首次检索需要拉取块元数据"
        assert self._meta_queries(second) == [], "窗口内第二次检索不再全表拉元数据（命中短缓存）"

    def test_write_path_invalidation_picks_up_new_chunk_immediately(self):
        """写入路径（rebuild_chunks/remove_chunks）显式失效：新块无需等 TTL。"""
        from ai.utils.index_meta import invalidate_index_meta

        hit_pks("数据库备份")  # 预热元数据缓存（不假设库中块集合为空）
        chunk = make_chunk("docs/meta-new.md", 0, CONTENT_A)
        invalidate_index_meta()  # 模拟 rebuild_chunks 的显式失效
        assert chunk.pk in hit_pks("数据库备份")

    def test_stale_window_heals_after_ttl(self, monkeypatch):
        """未显式失效（直改库）时，TTL 过期后签名比对自愈，不长期陈旧。"""
        from ai.utils import index_meta

        chunk = make_chunk("docs/meta-stale.md", 0, CONTENT_A)
        assert chunk.pk in hit_pks("数据库备份")
        monkeypatch.setattr(index_meta, "META_CACHE_TTL_SECONDS", 0)  # 下次读取即过期
        chunk.delete()
        assert chunk.pk not in hit_pks("数据库备份"), "TTL 过期后块删除应被发现"
