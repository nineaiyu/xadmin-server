# -*- coding: utf-8 -*-
"""dump_init_json 命令（loadjson 种子导出半环）测试。

loadjson/*.json 的消费侧（load_init_json / module 裁剪 / seed_demo_*）已有
种子测试覆盖，本文件补齐导出侧：登记模型即便空表也必须产出合法 JSON，
且行内容可回读（防止序列化字段口径回归导致导出半环损坏）。
"""

import json

import pytest
from django.core.management import call_command
from django.test import override_settings

from settings.models import Setting

pytestmark = pytest.mark.django_db

# 与 Command.model_names 一一对应（model_name.json）
SEED_FILES = [
    "userrole.json",
    "deptinfo.json",
    "menu.json",
    "menumeta.json",
    "systemconfig.json",
    "datapermission.json",
    "fieldpermission.json",
    "modellabelfield.json",
    "setting.json",
]


def test_dump_init_json_writes_valid_seed_files(tmp_path):
    Setting.objects.create(name="DUMP_TEST_FLAG", value="true", category="test")
    (tmp_path / "loadjson").mkdir()

    with override_settings(PROJECT_DIR=str(tmp_path)):
        call_command("dump_init_json")

    for filename in SEED_FILES:
        path = tmp_path / "loadjson" / filename
        assert path.exists(), f"缺少导出文件 {filename}"
        json.loads(path.read_text(encoding="utf-8"))

    rows = json.loads((tmp_path / "loadjson" / "setting.json").read_text(encoding="utf-8"))
    assert any(item["fields"]["name"] == "DUMP_TEST_FLAG" for item in rows)


def test_dump_init_json_empty_models_still_produce_valid_files(tmp_path):
    """空表模型也要产出文件（post_migrate 种子数据除外，仅验证合法性）。"""
    (tmp_path / "loadjson").mkdir()

    with override_settings(PROJECT_DIR=str(tmp_path)):
        call_command("dump_init_json")

    for filename in SEED_FILES:
        path = tmp_path / "loadjson" / filename
        assert path.exists(), f"缺少导出文件 {filename}"
        json.loads(path.read_text(encoding="utf-8"))
