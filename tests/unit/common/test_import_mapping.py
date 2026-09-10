# -*- coding: utf-8 -*-
"""列映射纯函数（common/core/import_mapping.py）与解析器接入（三入口同源）。"""

import io
from csv import reader as csv_reader
from unittest.mock import MagicMock

from rest_framework import serializers

from common.core.import_mapping import (
    apply_column_mapping,
    build_field_index,
    first_column_candidates,
    normalize_header,
    resolve_headers,
    writable_field_options,
)
from common.drf.parsers.base import BaseFileParser


class MappedSerializer(serializers.Serializer):
    """列映射用最小序列化器：字段名与中文 label 不同名。"""

    id = serializers.IntegerField(read_only=True, label="ID")
    username = serializers.CharField(label="用户名")
    nickname = serializers.CharField(label="昵称", required=False)

    class Meta:
        fields = ["id", "username", "nickname"]


class MappedCsvParser(BaseFileParser):
    serializer_cls = MappedSerializer

    def generate_rows(self, stream_data):
        return csv_reader(stream_data.decode("utf-8").splitlines())


def make_mapped_view(jms_context=None):
    view = MagicMock()
    view.get_serializer_class.return_value = MappedSerializer
    view.request.META = {}
    view.request.jms_context = jms_context if jms_context is not None else {}
    return view


def parse_csv(content, jms_context=None):
    parser = MappedCsvParser()
    view = make_mapped_view(jms_context)
    return parser.parse(io.BytesIO(content.encode("utf-8")), parser_context={"view": view})


class TestNormalizeHeader:
    def test_strip_and_case(self):
        assert normalize_header("  名称  ") == "名称"
        assert normalize_header("User Name") == "user_name"
        assert normalize_header("user-name") == "user_name"
        assert normalize_header("**名称**") == "名称"

    def test_paren_field_extracted(self):
        # 旧格式 `展示名(字段名)`：括号内即字段名
        assert normalize_header("用户名(pk)") == "pk"
        assert normalize_header("部门(dept_id)") == "dept_id"

    def test_none_and_non_str(self):
        assert normalize_header(None) == ""
        assert normalize_header(123) == "123"


class TestFieldIndex:
    def test_name_and_label_both_hit(self):
        index = build_field_index(MappedSerializer().fields)
        assert index["username"] == "username"
        assert index["用户名"] == "username"
        assert index["昵称"] == "nickname"

    def test_read_only_excluded_except_id(self):
        options = writable_field_options(MappedSerializer().fields)
        values = [item["value"] for item in options]
        assert "username" in values and "nickname" in values
        # id 可读可写例外保留（更新导入用），label 可作为展示名
        assert "id" in values
        labels = {item["value"]: item["label"] for item in options}
        assert labels["username"] == "用户名"


class TestFirstColumnCandidates:
    def test_candidates_by_label_and_name(self):
        fields = MappedSerializer().fields
        assert first_column_candidates(["用户名", "nickname", "无关列"], fields) == ["username", "nickname", ""]

    def test_no_fuzzy_inference(self):
        # 不做模糊/语义推断：不同名即无候选（错配比不匹配更危险）
        assert first_column_candidates(["账号"], MappedSerializer().fields) == [""]


class TestApplyColumnMapping:
    def test_mapped_columns_replaced(self):
        resolved, unmatched = apply_column_mapping(
            ["用户名列", "昵称列"], {"用户名列": "username", "昵称列": "nickname"}
        )
        assert resolved == ["username", "nickname"]
        assert unmatched == []

    def test_key_matching_is_normalized(self):
        resolved, _ = apply_column_mapping(["用户名 列"], {"用户名_列": "username"})
        assert resolved == ["username"]

    def test_ignore_tokens(self):
        mapping = {"列一": "", "列二": "-", "列三": "__ignore__", "列四": None}
        resolved, unmatched = apply_column_mapping(["列一", "列二", "列三", "列四"], mapping)
        assert resolved == ["", "", "", ""]
        assert unmatched == []

    def test_unmapped_dropped_when_ignore_unknown(self):
        resolved, unmatched = apply_column_mapping(["已知列", "未知列"], {"已知列": "username"})
        assert resolved == ["username", ""]
        assert unmatched == ["未知列"]

    def test_unmapped_kept_when_not_ignore_unknown(self):
        resolved, unmatched = apply_column_mapping(["已知列", "昵称"], {"已知列": "username"}, ignore_unknown=False)
        assert resolved == ["username", "昵称"]
        assert unmatched == ["昵称"]

    def test_non_str_key_ignored(self):
        resolved, _ = apply_column_mapping(["用户名"], {1: "username"})
        assert resolved == [""]

    def test_empty_mapping_equals_no_mapping(self):
        resolved, unmatched = apply_column_mapping(["用户名"], None)
        assert resolved == [""]
        assert unmatched == ["用户名"]


class TestResolveHeaders:
    def test_target_must_exist_in_fields(self):
        fields = MappedSerializer().fields
        field_names, unmatched = resolve_headers(["甲", "乙"], {"甲": "username", "乙": "not_exists"}, fields)
        assert field_names == ["username", ""]
        assert unmatched == []

    def test_label_target_also_resolves(self):
        fields = MappedSerializer().fields
        field_names, _ = resolve_headers(["甲"], {"甲": "用户名"}, fields)
        assert field_names == ["username"]

    def test_unmatched_resolves_by_label_when_not_ignored(self):
        fields = MappedSerializer().fields
        field_names, unmatched = resolve_headers(["甲", "昵称"], {"甲": "username"}, fields, ignore_unknown=False)
        assert field_names == ["username", "nickname"]
        assert unmatched == ["昵称"]


class TestParserMappingIntegration:
    """三入口共用同一解析链：映射在 parse() 处生效，行数据即目标字段名。"""

    content = "用户名列,昵称列,多余列\nadmin,管理员,x\n"

    def test_mapping_applied_and_unknown_dropped(self):
        data = parse_csv(
            self.content,
            {"import_mapping": {"mapping": {"用户名列": "username", "昵称列": "nickname"}, "ignore_unknown": True}},
        )
        assert data == [{"username": "admin", "nickname": "管理员"}]

    def test_ignore_unknown_false_resolves_label_and_records_unmatched(self):
        # 表头直接是 label（昵称）：未映射但 ignore_unknown=false 时仍可等名解析
        content = "用户名列,昵称,多余列\nadmin,管理员,x\n"
        jms_context = {"import_mapping": {"mapping": {"用户名列": "username"}, "ignore_unknown": False}}
        view = make_mapped_view(jms_context)
        parser = MappedCsvParser()
        data = parser.parse(
            io.BytesIO(content.encode("utf-8")),
            parser_context={"view": view},
        )
        assert data == [{"username": "admin", "nickname": "管理员"}]
        assert jms_context["import_unmatched_columns"] == ["昵称", "多余列"]

    def test_without_mapping_legacy_behavior(self):
        data = parse_csv("用户名,昵称\nadmin,管理员\n")
        assert data == [{"username": "admin", "nickname": "管理员"}]

    def test_empty_field_name_columns_not_in_row(self):
        # 未映射列在解析阶段即被丢弃，不允许出现空键
        data = parse_csv(self.content)
        assert all("" not in row for row in data)
