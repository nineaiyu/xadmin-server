# -*- coding: utf-8 -*-
"""向量纯函数单元测试：编解码 / 余弦相似度 / RRF 融合（无数据库、无外部服务）。"""

import math

from ai.utils.ai_embeddings import (
    RRF_K,
    TOKEN_WEIGHT,
    VECTOR_WEIGHT,
    cosine_similarity,
    decode_vector,
    encode_vector,
    rrf_fuse,
)


class TestEncodeDecode:
    def test_roundtrip_keeps_float32_values(self):
        values = [0.5, -1.25, 3.0]
        decoded = decode_vector(encode_vector(values))
        assert decoded is not None
        assert [round(item, 5) for item in decoded] == values

    def test_none_and_invalid_length(self):
        assert decode_vector(None) is None
        assert decode_vector(b"") is None
        assert decode_vector(b"\x01\x02\x03") is None

    def test_memoryview_input_supported(self):
        """psycopg 读回的 BinaryField 可能是 memoryview。"""
        blob = encode_vector([1.0, 2.0])
        assert decode_vector(memoryview(blob)) is not None


class TestCosine:
    def test_identical_and_orthogonal(self):
        assert abs(cosine_similarity([1.0, 2.0], [1.0, 2.0]) - 1.0) < 1e-9
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_opposite_direction_is_negative(self):
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0

    def test_invalid_inputs_return_zero(self):
        assert cosine_similarity([], [1.0]) == 0.0
        assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0
        assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


class TestRrfFuse:
    def test_single_channel_ranking(self):
        fused = rrf_fuse([7, 8], [], top_k=2)
        assert [pk for _score, pk in fused] == [7, 8]
        assert fused[0][0] > fused[1][0]
        assert abs(fused[0][0] - TOKEN_WEIGHT / (RRF_K + 1)) < 1e-12

    def test_both_channels_add_up(self):
        """两个通道都命中的块得分叠加，应排在仅单通道命中的块之前。"""
        fused = rrf_fuse([1, 5], [1, 9], top_k=3)
        assert fused[0][1] == 1
        scores = dict((pk, score) for score, pk in fused)
        assert scores[1] > scores[5] and scores[1] > scores[9]

    def test_vector_channel_weighted_below_token(self):
        """词频命中优先于单通道向量命中（弱 embedding 模型不稀释质量基线）。"""
        fused = rrf_fuse([5], [9], top_k=2)
        scores = dict((pk, score) for score, pk in fused)
        assert scores[5] > scores[9]
        assert abs(scores[9] - VECTOR_WEIGHT / (RRF_K + 1)) < 1e-12

    def test_vector_only_hit_is_included(self):
        """仅向量通道召回的块必须进入结果（语义召回的价值位）。"""
        fused = rrf_fuse([1], [42], top_k=2)
        assert {pk for _score, pk in fused} == {1, 42}

    def test_top_k_truncates_and_deterministic(self):
        fused = rrf_fuse([3, 1], [1, 3], top_k=1)
        assert len(fused) == 1
        # 词频名次更靠前者胜出（权重更高），结果可复现
        assert fused[0][1] == 3
        assert math.isclose(fused[0][0], TOKEN_WEIGHT / (RRF_K + 1) + VECTOR_WEIGHT / (RRF_K + 2), rel_tol=1e-12)
