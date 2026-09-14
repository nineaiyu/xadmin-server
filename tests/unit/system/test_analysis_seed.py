# -*- coding: utf-8 -*-
"""数据分析内置示例种子守护测试。

loadjson/dataset.json、dashboard.json、report.json、screen.json 是新装环境
的内置示例（load_init_json 按 loaddata upsert 灌入）。固定 pk 之间存在引用：
仪表盘卡片 → 数据集、报表外键 → 数据集、大屏 → 仪表盘。这里锁定三件事：

1. 数据集定义必须能通过白名单校验（绑定模型/列/排序/limit 与
   ModelLabelField 注册表一致，否则执行期 validate 直接失败）；
2. 种子间 pk 引用完整（改 pk 或漏文件时尽早暴露）；
3. 内置示例全部共享可见（普通用户打开即见），且报表默认停用
   （is_active=false，避免向占位邮箱定时发邮件）。
"""

import json
import os

import pytest
from django.conf import settings as dj_settings

from system.models import ModelLabelField
from system.models.dataset import Dataset
from system.utils.dataset import validate_dataset

LOADJSON_DIR = os.path.join(dj_settings.PROJECT_DIR, "loadjson")

DATASET_FIELDS_EXCLUDED = ("creator", "modifier", "dept_belong")


def _load(name: str):
    with open(os.path.join(LOADJSON_DIR, name), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def userinfo_whitelist(db):
    """system.userinfo 白名单（列取值与种子 columns 严格同源）。"""
    root, _ = ModelLabelField.objects.get_or_create(
        name="system.userinfo",
        defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "用户"},
    )
    for name in ("username", "nickname", "gender", "is_active", "phone", "dept", "date_joined"):
        ModelLabelField.objects.get_or_create(
            name=name,
            parent=root,
            defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": name},
        )
    return root


def test_seed_dataset_passes_whitelist_validation(userinfo_whitelist):
    """种子数据集定义必须通过保存侧整体校验（模型/列/过滤/排序/limit/config）。"""
    item = _load("dataset.json")[0]
    fields = {k: v for k, v in item["fields"].items() if k not in DATASET_FIELDS_EXCLUDED}
    validate_dataset(Dataset(**fields))


def test_seed_dataset_columns_within_whitelist(userinfo_whitelist):
    """种子 columns 与趋势 date_field 必须落在 userinfo 白名单内（防注册表漂移）。"""
    from system.utils.dataset import available_fields

    item = _load("dataset.json")[0]
    whitelist = set(available_fields("system.userinfo"))
    columns = item["fields"]["columns"]
    assert set(columns) <= whitelist
    date_field = (item["fields"].get("config") or {}).get("date_field")
    assert not date_field or date_field in whitelist


def test_seed_pk_references_are_consistent():
    """卡片→数据集、报表→数据集、大屏→仪表盘的固定 pk 引用必须完整。"""
    datasets = {item["pk"] for item in _load("dataset.json")}
    dashboards = {item["pk"] for item in _load("dashboard.json")}

    for dash in _load("dashboard.json"):
        for card in dash["fields"]["layout"]:
            assert card["dataset"] in datasets, f"卡片 {card['id']} 引用了不存在的数据集"
    for report in _load("report.json"):
        assert report["fields"]["dataset"] in datasets, "报表引用了不存在的数据集"
    for screen in _load("screen.json"):
        assert set(screen["fields"]["dashboards"]) <= dashboards, "大屏引用了不存在的仪表盘"


def test_seed_shared_visibility_and_report_inactive():
    """内置示例全部共享可见；报表默认停用（占位邮箱不参与定时派发）。"""
    for name in ("dataset.json", "dashboard.json", "screen.json"):
        for item in _load(name):
            assert item["fields"]["visibility"] == "shared", f"{name} 应为共享可见"
    for report in _load("report.json"):
        assert report["fields"]["is_active"] is False, "内置报表示例必须停用"
