"""AI 知识库检索评测（A1「评测驱动」）：评测集 hit@5 门禁入 CI。

评测口径（与 docs/plans/archive/下一年度规划建议-2027.10-2028.09.md §四.A1 一致）：
- 语料 = 仓库文档（system/utils/ai.py 的 _iter_doc_files，docs/**.md + 根 README/CONTRIBUTING）；
- 评测集 = tests/data/ai_retrieval_eval.json（问题 + 期望出处），命中 top-5 任一期望出处即 hit；
- 门禁：hit@5 ≥ 75%（低于阈值按规划升级向量检索）；
- 向量触发条件（分块数 > 500 或 hit@5 < 75%）的实测值随本测试输出，结论回填 docs/metrics.md。

维护约定：语料文档新增/重命名后，若期望出处失效（doc 被改名），下面的
test_eval_expected_paths_exist 会直接失败并列出失效项，避免评测静默失真。
"""

import json
from pathlib import Path

import pytest

from ai.utils.ai import _iter_doc_files, retrieve, sync_knowledge

EVAL_FILE = Path(__file__).resolve().parents[2] / "data" / "ai_retrieval_eval.json"
HIT_RATE_FLOOR = 0.75
TOP_K = 5
#: 向量升级触发线（分块规模），见 §四.A1
VECTOR_TRIGGER_CHUNKS = 500


def _load_eval() -> dict:
    return json.loads(EVAL_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def corpus(django_db_setup, django_db_blocker):
    """同步仓库文档入库一次，模块内复用（真实语料，与生产同源）。"""
    from ai.models.ai import AiKnowledgeChunk

    with django_db_blocker.unblock():
        summary = sync_knowledge()
        chunk_total = AiKnowledgeChunk.objects.count()
    return {**summary, "chunk_total": chunk_total}


def test_eval_expected_paths_exist(corpus):
    """评测集期望出处必须存在于当前语料（防文档改名导致评测静默失真）。"""
    corpus_paths = {rel_path for _, rel_path in _iter_doc_files()}
    stale = []
    for case in _load_eval()["cases"]:
        for expected in case["expected"]:
            if expected not in corpus_paths:
                stale.append(f"{case['id']} -> {expected}")
    assert not stale, "评测集出处已不在语料中（文档改名/删除需同步评测集）：\n" + "\n".join(stale)


@pytest.mark.django_db
def test_retrieval_hit_rate(corpus):
    """评测集 hit@5：命中期望出处即算对，聚合命中率必须 ≥ 75%（并记录单次检索均耗时）。"""
    import time

    cases = _load_eval()["cases"]
    assert cases, "评测集为空"

    hits = 0
    details = []
    elapsed = []
    for case in cases:
        started = time.perf_counter()
        retrieved = retrieve(case["question"], top_k=TOP_K)
        elapsed.append((time.perf_counter() - started) * 1000)
        paths = [item["chunk"].source_path for item in retrieved]
        hit = any(path in case["expected"] for path in paths)
        hits += 1 if hit else 0
        details.append(
            f"{'HIT ' if hit else 'MISS'} [{case['id']}] top{TOP_K}={paths or '[]'} expected={case['expected']}"
        )

    hit_rate = hits / len(cases)
    avg_ms = sum(elapsed) / len(elapsed)
    worst_ms = max(elapsed)
    # 指标随测试输出（CI 日志可回溯实测值）；失败时附带逐题明细
    print(
        f"\n[ai-retrieval-eval] hit@5 = {hits}/{len(cases)} = {hit_rate:.1%}"
        f" | chunks = {corpus['chunk_total']} | vector_trigger = {VECTOR_TRIGGER_CHUNKS}"
        f" | retrieve avg = {avg_ms:.1f}ms worst = {worst_ms:.1f}ms"
    )
    for line in details:
        if line.startswith("MISS"):
            print(line)
    # 全量扫描式检索的兜底性能护栏（只防数量级退化，不做严格耗时断言）
    assert worst_ms < 2000, f"单次检索最差 {worst_ms:.0f}ms，疑似语料规模/实现退化"
    assert hit_rate >= HIT_RATE_FLOOR, (
        f"检索 hit@5 {hit_rate:.1%} 低于门禁 {HIT_RATE_FLOOR:.0%}（按规划应升级向量检索）。\n" + "\n".join(details)
    )


@pytest.mark.django_db
def test_retrieval_scale_gate_recorded(corpus):
    """记录向量升级的规模门控实测值（分块数），供 metrics 回填与 ADR 决策引用。"""
    assert corpus["chunk_total"] > 0
    if corpus["chunk_total"] > VECTOR_TRIGGER_CHUNKS:
        pytest.skip(
            f"分块数 {corpus['chunk_total']} 已超向量触发线 {VECTOR_TRIGGER_CHUNKS}——"
            "按规划 §四.A1 需评估向量检索（本测试仅记录规模，不阻断）"
        )
