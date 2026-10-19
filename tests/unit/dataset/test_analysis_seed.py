# -*- coding: utf-8 -*-
"""数据分析内置示例种子守护测试。

示例数据在 ``dataset/seeds/sample/``（``load_init_json`` 默认不装，
``init_data`` 与 ``--with-sample`` 才装）。固定 pk 之间存在引用：
仪表盘卡片 → 数据集、报表外键 → 数据集、大屏 → 仪表盘。这里锁定四件事：

1. 数据集定义必须能通过白名单校验（绑定模型/列/排序/limit 与
   ModelLabelField 注册表一致，否则执行期 validate 直接失败）；
2. 种子间 pk 引用完整（改 pk 或漏文件时尽早暴露）；
3. 内置示例全部共享可见（普通用户打开即见），且报表默认停用
   （is_active=false，避免向占位邮箱定时发邮件）；
4. 示例只存在于 sample 层：默认装载计划不得包含它们（防止误把示例塞回契约层）。
"""

import pytest
from django.core.management import call_command

from dataset.models.dataset import Dataset
from dataset.utils.dataset import validate_dataset
from system.models import ModelLabelField
from system.services.seeding import build_plan, discover, read_model_rows

#: 示例文件 → 模型 label
SAMPLE_MODELS = {
    "dataset.json": "dataset.dataset",
    "dashboard.json": "dataset.dashboard",
    "screen.json": "dataset.screen",
    "report.json": "dataset.report",
}

DATASET_FIELDS_EXCLUDED = ("creator", "modifier", "dept_belong")


def _load(name: str):
    return read_model_rows(SAMPLE_MODELS[name], include_sample=True)


@pytest.fixture
def userinfo_whitelist(db):
    """identity.userinfo 白名单（列取值与种子 columns 严格同源）。"""
    root, _ = ModelLabelField.objects.get_or_create(
        name="identity.userinfo",
        defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "用户"},
    )
    for name in ("username", "nickname", "gender", "is_active", "phone", "dept", "date_joined"):
        ModelLabelField.objects.get_or_create(
            name=name,
            parent=root,
            defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": name},
        )
    return root


def test_examples_live_in_sample_layer_only():
    """默认装载计划不含示例数据；加 --with-sample 后才出现在 samples 里。"""

    assert not any(spec.model in SAMPLE_MODELS.values() for spec in build_plan(discover()).all_specs())
    plan = build_plan(discover(), include_sample=True)
    assert set(SAMPLE_MODELS.values()) <= {spec.model for spec in plan.samples}


def test_seed_dataset_passes_whitelist_validation(userinfo_whitelist):
    """种子数据集定义必须通过保存侧整体校验（模型/列/过滤/排序/limit/config）。"""
    item = _load("dataset.json")[0]
    fields = {k: v for k, v in item["fields"].items() if k not in DATASET_FIELDS_EXCLUDED}
    validate_dataset(Dataset(**fields))


def test_seed_dataset_columns_within_whitelist(userinfo_whitelist):
    """种子 columns 与趋势 date_field 必须落在 userinfo 白名单内（防注册表漂移）。"""
    from dataset.utils.dataset import available_fields

    item = _load("dataset.json")[0]
    whitelist = set(available_fields("identity.userinfo"))
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


@pytest.mark.django_db
def test_with_sample_loads_examples(seed_creator_user):
    """``--with-sample`` 装载示例数据（新装 init_data 走这条链路）。"""

    call_command("load_init_json", with_sample=True, verbosity=0)
    assert Dataset.objects.filter(name__startswith="示例-").exists()
