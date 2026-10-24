# -*- coding: utf-8 -*-
"""大屏序列化器布局校验（validate_layout / validate_free_layout）回归测试。

锁定指标卡窗格引用数据集的校验语义与查询行为：
- 引用未知数据集（含非法主键形态）一律以既有文案拒绝（窗格 id 定位）；
- sum/avg 取值列必须落在数据集数值列白名单内（与报表组件同口径）；
- 校验只加载被引用的数据集：不含指标卡的布局不查询数据集表；
- 自由载荷与栅格载荷共用同一套引用校验，并各自校验「与 layout_mode 的对应关系」。
"""

import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.translation import gettext
from rest_framework import serializers

from dataset.models.dataset import Dashboard, Dataset, Screen
from dataset.serializers.screen import ScreenSerializer

pytestmark = pytest.mark.django_db


@pytest.fixture
def dataset(db):
    return Dataset.objects.create(
        name="布局校验数据集",
        bound_model="identity.userinfo",
        columns=["username", "gender"],
        visibility="shared",
    )


def _pane(pk, pane_type, **extra):
    pane = {"pk": pk, "type": pane_type, "x": 0, "y": 0, "w": 3, "h": 2}
    pane.update(extra)
    return pane


def _free_pane(pk, pane_type, **extra):
    pane = {"pk": pk, "type": pane_type, "x": 0, "y": 0, "w": 320, "h": 200}
    pane.update(extra)
    return pane


def _metric_pane(dataset_pk, **extra):
    return [_pane("m1", "metric", dataset=dataset_pk, metric="sum", **extra)]


def test_metric_sum_with_numeric_value_field_passes(dataset):
    layout = ScreenSerializer().validate_layout(_metric_pane(str(dataset.pk), value_field="gender"))
    assert layout[0]["dataset"] == str(dataset.pk)
    assert layout[0]["value_field"] == "gender"


def test_metric_sum_with_non_numeric_value_field_rejected(dataset):
    with pytest.raises(serializers.ValidationError) as excinfo:
        ScreenSerializer().validate_layout(_metric_pane(str(dataset.pk), value_field="username"))
    assert "m1" in str(excinfo.value)


def test_metric_with_unknown_dataset_rejected(dataset):
    """引用的数据集不存在仍须拒绝：known 集为空时存在性校验会被结构校验跳过，依赖兜底语义。"""
    with pytest.raises(serializers.ValidationError) as excinfo:
        ScreenSerializer().validate_layout(_metric_pane(str(uuid.uuid4())))
    assert "m1" in str(excinfo.value)


def test_metric_with_malformed_dataset_reference_rejected(dataset):
    with pytest.raises(serializers.ValidationError):
        ScreenSerializer().validate_layout(_metric_pane("not-a-uuid"))


def test_layout_without_metric_panes_skips_dataset_queries(dataset):
    dashboard = Dashboard.objects.create(name="布局看板", layout=[], visibility="shared")
    serializer = ScreenSerializer()
    with CaptureQueriesContext(connection) as ctx:
        serializer.validate_layout([_pane("d1", "dashboard", dashboard=str(dashboard.pk))])
    assert not [q["sql"] for q in ctx.captured_queries if "dataset_dataset" in q["sql"]]


class TestFreeLayoutValidation:
    """自由载荷：类型字段与栅格版同规则，坐标段换像素语义。"""

    def test_design_defaults_and_panes_normalised(self):
        free = ScreenSerializer().validate_free_layout({"panes": [_free_pane("c1", "clock", x=40, y=40, w=200, h=120)]})
        assert free["design_w"] == 1920 and free["design_h"] == 1080
        assert free["panes"][0] == {"pk": "c1", "type": "clock", "x": 40, "y": 40, "w": 200, "h": 120, "size": 40}

    def test_empty_payload_normalised_to_empty_dict(self):
        assert ScreenSerializer().validate_free_layout({}) == {}
        assert ScreenSerializer().validate_free_layout(None) == {}

    def test_metric_value_field_checked_against_dataset(self, dataset):
        free = ScreenSerializer().validate_free_layout(
            {"panes": [_free_pane("m1", "metric", dataset=str(dataset.pk), metric="sum", value_field="gender")]}
        )
        assert free["panes"][0]["value_field"] == "gender"
        with pytest.raises(serializers.ValidationError) as excinfo:
            ScreenSerializer().validate_free_layout(
                {"panes": [_free_pane("m1", "metric", dataset=str(dataset.pk), metric="sum", value_field="username")]}
            )
        assert "m1" in str(excinfo.value)

    def test_unknown_dataset_rejected(self, dataset):
        with pytest.raises(serializers.ValidationError) as excinfo:
            ScreenSerializer().validate_free_layout(
                {"panes": [_free_pane("m1", "metric", dataset=str(uuid.uuid4()), metric="count")]}
            )
        assert "m1" in str(excinfo.value)

    def test_unknown_dashboard_rejected(self):
        with pytest.raises(serializers.ValidationError):
            ScreenSerializer().validate_free_layout(
                {"panes": [_free_pane("d1", "dashboard", dashboard=str(uuid.uuid4()))]}
            )

    def test_pane_below_minimum_size_rejected(self):
        with pytest.raises(serializers.ValidationError):
            ScreenSerializer().validate_free_layout({"panes": [_free_pane("p1", "text", w=15, h=100)]})


class TestThemeValidation:
    """投屏主题字段：深色缺省（存量观感）、非白名单拒绝、主题色 hex 归一。"""

    def test_theme_defaults_to_dark(self):
        screen = Screen(name="主题缺省")
        assert screen.theme == "dark"
        assert screen.theme_color == ""

    @pytest.mark.parametrize("value", ["dark", "light", "auto"])
    def test_theme_accepts_whitelist(self, value):
        assert ScreenSerializer().validate_theme(value) == value

    @pytest.mark.parametrize("value", [None, "", "  "])
    def test_blank_theme_falls_back_to_dark(self, value):
        assert ScreenSerializer().validate_theme(value) == "dark"

    def test_theme_case_and_padding_normalised(self):
        assert ScreenSerializer().validate_theme(" LIGHT ") == "light"

    @pytest.mark.parametrize("value", ["system", "night", "default"])
    def test_invalid_theme_rejected(self, value):
        with pytest.raises(serializers.ValidationError):
            ScreenSerializer().validate_theme(value)

    def test_theme_color_short_hex_expanded_lowercase(self):
        assert ScreenSerializer().validate_theme_color("#ABC") == "#aabbcc"

    def test_theme_color_full_hex_lowercased(self):
        assert ScreenSerializer().validate_theme_color("#006BE6") == "#006be6"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_blank_theme_color_means_follow_site(self, value):
        assert ScreenSerializer().validate_theme_color(value) == ""

    @pytest.mark.parametrize("value", ["blue", "#12345", "rgb(1,2,3)", "#gggggg", "006be6"])
    def test_invalid_theme_color_rejected(self, value):
        with pytest.raises(serializers.ValidationError):
            ScreenSerializer().validate_theme_color(value)


class TestLayoutModeConsistency:
    """写入载荷必须与（合并后的）layout_mode 对应，避免静默回退到另一形态。"""

    def test_free_payload_requires_free_mode(self):
        with pytest.raises(serializers.ValidationError) as excinfo:
            ScreenSerializer().validate({"free_layout": {"panes": [_free_pane("p1", "clock")]}})
        # 文案断言取 gettext 同源值：语言包存在时 str() 解析为译文（中文环境恰好不含 "free"）
        assert gettext("Free layout requires free mode") in str(excinfo.value)

    def test_free_payload_allowed_in_free_mode(self):
        attrs = {"layout_mode": "free", "free_layout": {"panes": [_free_pane("p1", "clock")]}}
        assert ScreenSerializer().validate(attrs) == attrs

    def test_grid_payload_rejected_in_free_mode(self):
        with pytest.raises(serializers.ValidationError):
            ScreenSerializer().validate({"layout_mode": "free", "layout": [_pane("p1", "clock")]})

    def test_mode_switch_alone_is_allowed(self):
        """切换模式只写 layout_mode：另一模式载荷原样保留，不触发一致性拒绝。"""
        attrs = {"layout_mode": "free"}
        assert ScreenSerializer().validate(attrs) == attrs

    def test_patch_grid_payload_on_free_screen_rejected(self):
        screen = Screen(name="自由模式大屏", dashboards=[], layout=[], layout_mode="free")
        serializer = ScreenSerializer(instance=screen)
        with pytest.raises(serializers.ValidationError):
            serializer.validate({"layout": [_pane("p1", "clock")]})

    def test_empty_payloads_allowed_in_any_mode(self):
        """清空载荷（layout=[] / free_layout={}）不构成「与模式不匹配」。"""
        assert ScreenSerializer(instance=Screen(layout_mode="free")).validate({"layout": []}) == {"layout": []}
        assert ScreenSerializer().validate({"layout_mode": "grid", "free_layout": {}}) == {
            "layout_mode": "grid",
            "free_layout": {},
        }
