# -*- coding: utf-8 -*-
"""种子多对多关系的批量落库：语义等价、幂等与语句数护栏。

背景：``loaddata`` 逐行处理 m2m（读关系 JOIN + DELETE + 多值 INSERT 三连）。完整种子
近 500 行带 m2m、关系数近五千，一次装载就要发上千条小往返；CI 4 核 runner 上这些 JOIN
劣化到秒级后，种子测试被拖成分钟级（全量 pytest 尾部假死的直接燃料）。

装配层因此把 m2m 从 fixture 抽出，装载后按「父行一次 DELETE + 一次多值 INSERT」落库。
本文件锁定三件事：**等价**（库内关系 == 种子声明）、**幂等**（重复装载不重复建行）、
**语句数不回升**（批量写退化回逐行写时立即失败）。
"""

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from system.models import FieldPermission, Menu, ModelLabelField
from system.services.seeding import (
    SeedM2MBatch,
    apply_m2m_relations,
    build_plan,
    discover,
    extract_m2m_relations,
    read_specs,
)

pytestmark = pytest.mark.django_db

MENU_PK = "5eed0a01-0000-4000-8000-000000000001"
MENU_PK_EMPTY = "5eed0a01-0000-4000-8000-000000000002"
FIELD_PK = "5eed0a01-0000-4000-8000-000000000011"
FIELD_PK_NEW = "5eed0a01-0000-4000-8000-000000000012"


class TestExtractM2MRelations:
    """装配阶段只搬移列表，不改语义。"""

    def test_lists_leave_rows_and_keep_all_parents(self):
        rows = {
            "system.menu": [
                {"model": "system.menu", "pk": MENU_PK, "fields": {"name": "A", "model": [FIELD_PK]}},
                # 空列表同样是「声明」：覆盖式语义要求清空该行既有关系
                {"model": "system.menu", "pk": MENU_PK_EMPTY, "fields": {"name": "B", "model": []}},
            ]
        }

        trimmed, batches = extract_m2m_relations(rows)

        assert all("model" not in row["fields"] for row in trimmed["system.menu"])
        assert len(batches) == 1
        batch = batches[0]
        assert (batch.model, batch.field_name) == ("system.menu", "model")
        assert batch.parents == [MENU_PK, MENU_PK_EMPTY]
        assert batch.pairs == [(MENU_PK, FIELD_PK)]

    def test_explicit_through_models_stay_with_loaddata(self):
        """显式中间表（如部门主管任命）可能带额外列，不套用 ``set()`` 语义。"""

        rows = {"identity.deptinfo": [{"model": "identity.deptinfo", "pk": MENU_PK, "fields": {"managers": ["1"]}}]}

        trimmed, batches = extract_m2m_relations(rows)

        assert batches == []
        assert trimmed["identity.deptinfo"][0]["fields"]["managers"] == ["1"]

    def test_model_without_m2m_is_untouched(self):
        rows = {"system.systemconfig": [{"model": "system.systemconfig", "pk": "1", "fields": {"value": ["a", "b"]}}]}

        trimmed, batches = extract_m2m_relations(rows)

        assert batches == []
        assert trimmed["system.systemconfig"][0]["fields"]["value"] == ["a", "b"]


class TestApplyM2MRelations:
    def test_overwrite_semantics(self, menu_factory):
        menu = menu_factory("批量写入菜单")
        first = ModelLabelField.objects.create(name="f1", field_type=ModelLabelField.FieldChoices.ROLE)
        second = ModelLabelField.objects.create(name="f2", field_type=ModelLabelField.FieldChoices.ROLE)

        written = apply_m2m_relations(
            [
                SeedM2MBatch(
                    model="system.menu",
                    field_name="model",
                    parents=[str(menu.pk)],
                    pairs=[(str(menu.pk), str(first.pk))],
                )
            ]
        )
        assert written == 1
        assert set(menu.model.values_list("pk", flat=True)) == {first.pk}

        # 再装载只声明第二个字段：首个字段必须被清掉（与 loaddata 的 set() 一致）
        apply_m2m_relations(
            [
                SeedM2MBatch(
                    model="system.menu",
                    field_name="model",
                    parents=[str(menu.pk)],
                    pairs=[(str(menu.pk), str(second.pk))],
                )
            ]
        )
        assert set(menu.model.values_list("pk", flat=True)) == {second.pk}

    def test_empty_list_clears_existing_relations(self, menu_factory):
        menu = menu_factory("清空关系菜单")
        field = ModelLabelField.objects.create(name="f3", field_type=ModelLabelField.FieldChoices.ROLE)
        menu.model.add(field)

        apply_m2m_relations([SeedM2MBatch(model="system.menu", field_name="model", parents=[str(menu.pk)], pairs=[])])

        assert menu.model.count() == 0

    def test_absent_parent_is_skipped_not_fatal(self, menu_factory):
        """装配被替换（单元测试捕获标签）或部分装载时父行可能不在库中：跳过而非撞外键。"""

        missing = "5eed0a01-0000-4000-8000-0000000000ff"
        field = ModelLabelField.objects.create(name="f4", field_type=ModelLabelField.FieldChoices.ROLE)

        written = apply_m2m_relations(
            [SeedM2MBatch(model="system.menu", field_name="model", parents=[missing], pairs=[(missing, str(field.pk))])]
        )

        assert written == 0
        assert Menu.model.through.objects.count() == 0


class TestSeedLoadM2MBudget:
    """整体装载的护栏：关系与种子声明等价、重复装载幂等、语句数不回升。"""

    @staticmethod
    def _seed_rows(model_label):
        rows_by_model, _non_empty = read_specs(build_plan(discover()).all_specs())
        return rows_by_model.get(model_label, [])

    def test_relations_match_seed_declaration(self, seed_creator_user):
        call_command("load_init_json", verbosity=0)

        expected = {
            (str(row["pk"]), str(item))
            for row in self._seed_rows("system.menu")
            for item in (row["fields"].get("model") or [])
        }
        actual = {
            (str(menu), str(field))
            for menu, field in Menu.model.through.objects.values_list("menu_id", "modellabelfield_id")
        }
        assert expected, "前置：种子应声明菜单与模型的绑定"
        assert actual == expected

        expected_fields = {
            (str(row["pk"]), str(item))
            for row in self._seed_rows("system.fieldpermission")
            for item in (row["fields"].get("field") or [])
        }
        actual_fields = {
            (str(perm), str(field))
            for perm, field in FieldPermission.field.through.objects.values_list(
                "fieldpermission_id", "modellabelfield_id"
            )
        }
        assert expected_fields, "前置：种子应声明字段白名单"
        # 收尾补齐会给「获权限点但无白名单」的角色追加全字段，种子声明必须是子集
        assert expected_fields <= actual_fields

    def test_m2m_writes_are_batched(self, seed_creator_user):
        with CaptureQueriesContext(connection) as ctx:
            call_command("load_init_json", verbosity=0)

        sql = [query["sql"] for query in ctx.captured_queries]
        menu_model = [item for item in sql if '"system_menu_model"' in item]
        field_permission = [item for item in sql if '"system_fieldpermission_field"' in item]

        # 逐行 set() 时分别是 ~700 / ~260 条；批量化后只有「查父行 + 清空 + 少量多值插入」
        assert len(menu_model) <= 8, f"菜单模型绑定退回逐行写入：{len(menu_model)} 条语句"
        assert len(field_permission) <= 60, f"字段白名单退回逐行写入：{len(field_permission)} 条语句"

    def test_repeated_load_is_idempotent(self, seed_creator_user):
        call_command("load_init_json", verbosity=0)
        before = (
            Menu.model.through.objects.count(),
            FieldPermission.field.through.objects.count(),
        )

        call_command("load_init_json", verbosity=0)

        after = (
            Menu.model.through.objects.count(),
            FieldPermission.field.through.objects.count(),
        )
        assert before == after
        assert before[0] > 0
