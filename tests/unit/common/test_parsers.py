# -*- coding: utf-8 -*-
"""common/drf/parsers/base.py：导入文件解析器（行列转换、字段映射、值解析）。"""
import io
from csv import reader as csv_reader
from unittest.mock import MagicMock

import pytest
from rest_framework import serializers
from rest_framework.exceptions import ParseError

from common.core.fields import BasePrimaryKeyRelatedField, LabeledChoiceField
from common.drf.parsers.base import (
    BaseFileParser,
    FileContentOverflowedError,
)


class RowSerializer(serializers.Serializer):
    """导入用最小序列化器：覆盖各 parse_value 分支。"""

    id = serializers.IntegerField(read_only=True, label="ID")
    name = serializers.CharField(label="名称")
    enabled = serializers.BooleanField(label="启用")
    config = serializers.JSONField(label="配置")
    remark = serializers.CharField(label="备注", allow_null=True)
    tags = serializers.ListField(child=serializers.CharField(), label="标签")

    class Meta:
        fields = [
            "id", "name", "enabled", "config", "remark", "tags",
        ]


class ChoiceSerializer(serializers.Serializer):
    user = BasePrimaryKeyRelatedField(label="用户")
    users = BasePrimaryKeyRelatedField(many=True, label="用户列表")
    labeled = LabeledChoiceField(choices=[(1, "一")], label="标签选择")
    plain = serializers.ChoiceField(choices=["a", "b"], label="普通选择")

    class Meta:
        fields = ["user", "users", "labeled", "plain"]


class CsvParser(BaseFileParser):
    serializer_cls = RowSerializer

    def generate_rows(self, stream_data):
        return csv_reader(stream_data.decode("utf-8").splitlines())


def make_view(meta=None):
    view = MagicMock()
    view.get_serializer_class.return_value = RowSerializer
    view.request.META = meta or {}
    view.request.jms_context = {}
    return view


class TestContentLength:
    def test_within_limit_passes(self):
        parser = CsvParser()
        parser.check_content_length({"CONTENT_LENGTH": "100"})

    def test_overflow_raises(self):
        parser = CsvParser()
        with pytest.raises(FileContentOverflowedError):
            parser.check_content_length(
                {"CONTENT_LENGTH": str(parser.FILE_CONTENT_MAX_LENGTH + 1)}
            )

    def test_http_header_fallback(self):
        parser = CsvParser()
        parser.check_content_length({"HTTP_CONTENT_LENGTH": "10"})


def test_get_stream_data_strips_bom():
    stream = io.BytesIO(b"\xef\xbb\xbf" + b"name")
    assert BaseFileParser.get_stream_data(stream) == b"name"


class TestConvertToFieldNames:
    def setup_method(self):
        self.parser = CsvParser()
        self.parser.serializer_fields = RowSerializer().fields

    def test_label_maps_to_field(self):
        assert self.parser.convert_to_field_names(["名称"]) == ["name"]

    def test_field_name_maps_to_itself(self):
        assert self.parser.convert_to_field_names(["name"]) == ["name"]

    def test_parenthesis_pattern_extracts_inner_field(self):
        assert self.parser.convert_to_field_names(["用户(user)"]) == ["user"]

    def test_star_prefix_stripped(self):
        assert self.parser.convert_to_field_names(["*名称"]) == ["name"]

    def test_read_only_id_allowed(self):
        assert self.parser.convert_to_field_names(["ID"]) == ["id"]

    def test_unknown_column_maps_to_empty(self):
        assert self.parser.convert_to_field_names(["未知列"]) == [""]


class TestLoadRow:
    def test_chinese_quotes_normalized(self):
        row = BaseFileParser.load_row(["“x”"])
        assert row == ['"x"']

    def test_non_string_passthrough(self):
        assert BaseFileParser.load_row([1, None]) == [1, None]

    def test_json_list_and_dict_parsed(self):
        row = BaseFileParser.load_row(['["a","b"]', '{"k": 1}'])
        assert row == [["a", "b"], {"k": 1}]

    def test_invalid_json_left_as_string(self):
        assert BaseFileParser.load_row(["[not-json"]) == ["[not-json"]


class TestIdNameToObj:
    def setup_method(self):
        self.parser = CsvParser()

    def test_empty_or_non_string(self):
        assert self.parser.id_name_to_obj("") == ""
        assert self.parser.id_name_to_obj(None) is None
        assert self.parser.id_name_to_obj(5) == 5

    def test_name_id_pattern(self):
        assert self.parser.id_name_to_obj("张三(2)") == {"pk": 2, "name": "张三"}

    def test_uuid_pattern_kept_string(self):
        uuid = "3f2b1c6e-1a2b-3c4d-5e6f-7a8b9c0d1e2f"
        assert self.parser.id_name_to_obj(f"obj({uuid})") == {
            "pk": uuid, "name": "obj",
        }

    def test_no_match_returns_value(self):
        assert self.parser.id_name_to_obj("plain") == "plain"


class TestParseValue:
    def setup_method(self):
        self.parser = CsvParser()
        self.parser.serializer_fields = RowSerializer().fields
        self.choice_parser = CsvParser()
        self.choice_parser.serializer_fields = ChoiceSerializer().fields

    def test_dash_with_allow_null_returns_none(self):
        field = RowSerializer().fields["remark"]
        assert self.parser.parse_value(field, "-") is None

    def test_boolean_field(self):
        field = RowSerializer().fields["enabled"]
        assert self.parser.parse_value(field, "True") is True
        assert self.parser.parse_value(field, "yes") is True
        assert self.parser.parse_value(field, "0") is False

    def test_json_field(self):
        field = RowSerializer().fields["config"]
        assert self.parser.parse_value(field, '{"a": 1}') == {"a": 1}
        assert self.parser.parse_value(field, "yes") is True
        assert self.parser.parse_value(field, "no") is False

    def test_charfield_non_string_json_dumps(self):
        field = RowSerializer().fields["name"]
        assert self.parser.parse_value(field, 123) == "123"

    def test_list_field_recursion(self):
        field = RowSerializer().fields["tags"]
        assert self.parser.parse_value(field, ["a", 1]) == ["a", "1"]

    def test_related_field_single(self):
        field = ChoiceSerializer().fields["user"]
        assert self.choice_parser.parse_value(field, "张三(2)") == {
            "pk": 2, "name": "张三",
        }

    def test_related_field_many(self):
        field = ChoiceSerializer().fields["users"]
        assert self.choice_parser.parse_value(field, ["a(1)", "b(2)"]) == [
            {"pk": 1, "name": "a"}, {"pk": 2, "name": "b"},
        ]

    def test_labeled_choice_hits_choicefield_branch(self):
        # LabeledChoiceField 继承自 serializers.ChoiceField，
        # parse_value 的 elif 链先命中 ChoiceField 分支原样返回
        field = ChoiceSerializer().fields["labeled"]
        assert self.choice_parser.parse_value(field, "一(1)") == "一(1)"

    def test_plain_choice_passthrough(self):
        field = ChoiceSerializer().fields["plain"]
        assert self.choice_parser.parse_value(field, "a") == "a"


class TestGenerateData:
    def setup_method(self):
        self.parser = CsvParser()
        self.parser.serializer_fields = RowSerializer().fields

    def test_empty_rows_skipped(self):
        # 表头由 parse() 阶段消费，generate_data 只接收数据行
        rows = [["", ""], ["任务A", "true"]]
        data = self.parser.generate_data(["name", "enabled"], rows)
        assert data == [{"name": "任务A", "enabled": True}]

    def test_rows_processed(self):
        rows = [["任务A", "true", '["x"]']]
        data = self.parser.generate_data(["name", "enabled", "config"], rows)
        assert data == [{"name": "任务A", "enabled": True, "config": ["x"]}]

    def test_pop_help_text(self):
        rows = [["#Help 说明", ""], ["名称"], ["x"]]
        assert BaseFileParser.pop_help_text_if_need(rows) == [["名称"], ["x"]]
        assert BaseFileParser.pop_help_text_if_need([]) == []
        assert BaseFileParser.pop_help_text_if_need([["名称"]]) == [["名称"]]


class TestFullParse:
    def test_parse_csv_stream(self):
        # 行序：表头（被 get_column_titles 消费）→ #Help 行（被 pop 跳过）→ 数据
        content = (
            "名称,启用,配置,备注\n"
            "#Help 导入说明\n"
            "任务A,true,{\"k\": 1},-\n"
            "任务B,0,\"[1,2]\",\n"
        )
        parser = CsvParser()
        view = make_view({"CONTENT_LENGTH": str(len(content))})
        data = parser.parse(
            io.BytesIO(content.encode("utf-8")),
            parser_context={"view": view},
        )
        assert data[0]["name"] == "任务A"
        assert data[0]["enabled"] is True
        assert data[0]["config"] == {"k": 1}
        assert data[0]["remark"] is None
        assert data[1]["enabled"] is False
        # jms_context 提供列标题与字段映射
        pairs = dict(view.request.jms_context["column_title_field_pairs"])
        assert pairs["名称"] == "name"

    def test_parse_invalid_serializer_raises_parse_error(self):
        parser = CsvParser()
        view = MagicMock()
        view.get_serializer_class.side_effect = RuntimeError("boom")
        with pytest.raises(ParseError):
            parser.parse(
                io.BytesIO(b"name\nx"),
                parser_context={"view": view},
            )

    def test_parse_bad_stream_raises_parse_error(self):
        class BrokenParser(CsvParser):
            def generate_rows(self, stream_data):
                raise ValueError("bad file")

        content = "名称\n任务A\n"
        parser = BrokenParser()
        view = make_view({"CONTENT_LENGTH": str(len(content))})
        with pytest.raises(ParseError):
            parser.parse(
                io.BytesIO(content.encode("utf-8")),
                parser_context={"view": view},
            )
