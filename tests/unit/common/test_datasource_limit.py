# -*- coding: utf-8 -*-
"""数据源接口量级上限：截断语义 + 超限提示（下拉/选项类小集合接口的统一保护）。"""

import pytest

from common.utils.datasource import DATASOURCE_MAX_ROWS, limit_datasource, truncation_detail
from system.models import Menu

pytestmark = pytest.mark.django_db


def _make_menu(menu_factory, index):
    return menu_factory(f"list:Probe{index}", path=f"api/system/probe{index}$", method="GET")


def test_limit_datasource_within_limit(menu_factory):
    """未超限：全量返回且不标记截断（不额外 COUNT，多取一条即为探测）。"""
    _make_menu(menu_factory, 1)
    rows, truncated = limit_datasource(Menu.objects.order_by("pk"), name="probe")
    assert truncated is False
    assert len(rows) == 1
    assert DATASOURCE_MAX_ROWS == 200


def test_limit_datasource_truncates_with_notice(monkeypatch, menu_factory):
    """超限：只回传前 N 条并给出可读提示（按运行期常量读取，便于按部署调整）。"""
    monkeypatch.setattr("common.utils.datasource.DATASOURCE_MAX_ROWS", 2)
    for index in range(3):
        _make_menu(menu_factory, index)

    rows, truncated = limit_datasource(Menu.objects.order_by("pk"), name="probe")
    assert truncated is True
    assert len(rows) == 2
    assert "2" in truncation_detail()
