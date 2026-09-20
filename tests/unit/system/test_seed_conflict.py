# -*- coding: utf-8 -*-
"""种子装配的冲突预检（system/utils/seed.py）单元测试。

守护：库内数据优先——自然键被占用时跳过种子行并级联处理引用；无冲突时零改动
（直接用仓库原始种子文件）。

背景：loaddata 把整次导入放在单个事务里，一行冲突会回滚**全部** fixture（含菜单与
权限点）。历史现场：开箱模板命令按 code 重建了内置种子的示例流程，导致新装/升级时
重灌种子整体失败。
"""

import json
import os

import pytest
from django.conf import settings as dj_settings

from system.management.commands.load_init_json import Command as LoadInitJsonCommand
from system.models import DataDict, UserRole
from system.models.approval import ApprovalFlow, ApprovalFlowNode, ApprovalFlowVersion
from system.utils.seed import _unique_checks, build_seed_fixtures, filter_conflicting_rows

pytestmark = pytest.mark.django_db

LOADJSON_DIR = os.path.join(dj_settings.PROJECT_DIR, "loadjson")
EXPENSE_SEED_PK = "5eed0006-0000-4000-8000-000000000002"
LEAVE_SEED_PK = "5eed0006-0000-4000-8000-000000000003"


def _read(name):
    with open(os.path.join(LOADJSON_DIR, name), encoding="utf-8") as fp:
        return json.load(fp)


class TestFilterConflictingRows:
    def test_no_conflict_keeps_rows(self):
        rows = {
            "system.approvalflow": [
                {
                    "model": "system.approvalflow",
                    "pk": EXPENSE_SEED_PK,
                    "fields": {"code": "brand_new", "name": "新流程"},
                }
            ]
        }
        filtered, notes = filter_conflicting_rows(rows)
        assert notes == []
        assert filtered["system.approvalflow"] == rows["system.approvalflow"]

    def test_same_pk_is_not_conflict(self):
        ApprovalFlow.objects.create(pk=EXPENSE_SEED_PK, code="demo_expense", name="同一条")
        rows = {
            "system.approvalflow": [
                {
                    "model": "system.approvalflow",
                    "pk": EXPENSE_SEED_PK,
                    "fields": {"code": "demo_expense", "name": "同一条"},
                }
            ]
        }
        filtered, notes = filter_conflicting_rows(rows)
        assert notes == []
        assert len(filtered["system.approvalflow"]) == 1

    def test_conflicting_natural_key_dropped(self):
        ApprovalFlow.objects.create(pk="cd81e9b1-f744-495d-b0ba-5d3b2273752a", code="demo_expense", name="库内对象")
        rows = {
            "system.approvalflow": [
                {
                    "model": "system.approvalflow",
                    "pk": EXPENSE_SEED_PK,
                    "fields": {"code": "demo_expense", "name": "种子"},
                }
            ]
        }
        filtered, notes = filter_conflicting_rows(rows)
        assert filtered["system.approvalflow"] == []
        assert len(notes) == 1
        assert "demo_expense" in notes[0] and "demo_expense" in notes[0]

    def test_cascade_drops_referencing_rows(self):
        ApprovalFlow.objects.create(pk="cd81e9b1-f744-495d-b0ba-5d3b2273752a", code="demo_expense", name="库内对象")
        rows = {
            "system.approvalflow": [
                {
                    "model": "system.approvalflow",
                    "pk": EXPENSE_SEED_PK,
                    "fields": {"code": "demo_expense", "name": "种子"},
                },
                {"model": "system.approvalflow", "pk": LEAVE_SEED_PK, "fields": {"code": "leave", "name": "请假审批"}},
            ],
            "system.approvalflownode": [
                {
                    "model": "system.approvalflownode",
                    "pk": "node-a",
                    "fields": {"flow": EXPENSE_SEED_PK, "name": "节点A"},
                },
                {
                    "model": "system.approvalflownode",
                    "pk": "node-b",
                    "fields": {"flow": LEAVE_SEED_PK, "name": "节点B"},
                },
            ],
        }
        filtered, notes = filter_conflicting_rows(rows)
        assert [row["pk"] for row in filtered["system.approvalflow"]] == [LEAVE_SEED_PK]
        assert [row["pk"] for row in filtered["system.approvalflownode"]] == ["node-b"]
        assert len(notes) == 2

    def test_m2m_reference_trimmed(self):
        role = _read("userrole.json")[0]
        role_pk, role_code = role["pk"], role["fields"]["code"]
        UserRole.objects.create(pk="aaaa1111-0000-0000-0000-000000000000", code=role_code, name="库内角色")
        rows = {
            "system.userrole": [
                {
                    "model": "system.userrole",
                    "pk": role_pk,
                    "fields": {"code": role_code, "name": "种子角色", "menu": []},
                }
            ],
            "system.datamaskrule": [
                {"model": "system.datamaskrule", "pk": "rule-1", "fields": {"name": "规则", "roles": [role_pk]}}
            ],
        }
        filtered, notes = filter_conflicting_rows(rows)
        assert filtered["system.userrole"] == []
        assert filtered["system.datamaskrule"][0]["fields"]["roles"] == []
        assert any("datamaskrule" in note for note in notes)

    def test_composite_unique_conflict_dropped(self):
        """组合唯一键 ``(flow, order)`` 被占用时同样跳过。

        历史缺陷：只判单字段唯一键，组合键漏判 → ``loaddata`` 撞唯一约束，
        单事务回滚**全部**种子（含菜单与权限点），init_data 直接失败。
        """
        ApprovalFlow.objects.create(pk=LEAVE_SEED_PK, code="leave", name="请假审批")
        ApprovalFlowNode.objects.create(
            pk="0ba28b97-24e2-4d34-a87a-787626fc5611", flow_id=LEAVE_SEED_PK, order=1, name="库内节点"
        )
        rows = {
            "system.approvalflownode": [
                {
                    "model": "system.approvalflownode",
                    "pk": "5eed0007-0000-4000-8000-000000000005",
                    "fields": {"flow": LEAVE_SEED_PK, "order": 1, "name": "种子节点"},
                }
            ]
        }
        filtered, notes = filter_conflicting_rows(rows)
        assert filtered["system.approvalflownode"] == []
        assert len(notes) == 1
        assert "flow=" in notes[0] and "order=1" in notes[0]

    def test_composite_unique_same_pk_kept(self):
        ApprovalFlow.objects.create(pk=LEAVE_SEED_PK, code="leave", name="请假审批")
        node_pk = "5eed0007-0000-4000-8000-000000000005"
        ApprovalFlowNode.objects.create(pk=node_pk, flow_id=LEAVE_SEED_PK, order=1, name="同一条")
        rows = {
            "system.approvalflownode": [
                {
                    "model": "system.approvalflownode",
                    "pk": node_pk,
                    "fields": {"flow": LEAVE_SEED_PK, "order": 1, "name": "同一条"},
                }
            ]
        }
        filtered, notes = filter_conflicting_rows(rows)
        assert notes == []
        assert len(filtered["system.approvalflownode"]) == 1

    def test_composite_unique_null_value_kept(self):
        """组合键含 NULL 时不判冲突（PG 的 UNIQUE 视 NULL 互不相等）。"""
        ApprovalFlow.objects.create(pk=LEAVE_SEED_PK, code="leave", name="请假审批")
        ApprovalFlowNode.objects.create(
            pk="0ba28b97-24e2-4d34-a87a-787626fc5611", flow_id=LEAVE_SEED_PK, order=1, name="库内节点"
        )
        rows = {
            "system.approvalflownode": [
                {
                    "model": "system.approvalflownode",
                    "pk": "node-no-order",
                    "fields": {"flow": LEAVE_SEED_PK, "order": None, "name": "缺序节点"},
                }
            ]
        }
        filtered, notes = filter_conflicting_rows(rows)
        assert notes == []
        assert len(filtered["system.approvalflownode"]) == 1


class TestUniqueChecks:
    def test_composite_constraints_included(self):
        """组合唯一约束必须进入检查清单（单字段以外的约束不得被静默跳过）。"""
        assert ("flow", "order") in {names for names, _ in _unique_checks(ApprovalFlowNode)}
        assert ("flow", "version") in {names for names, _ in _unique_checks(ApprovalFlowVersion)}
        assert ("parent", "code") in {names for names, _ in _unique_checks(DataDict)}


class TestBuildSeedFixtures:
    def test_clean_db_uses_original_files(self, tmp_path):
        labels, notes, trimmed = build_seed_fixtures(LoadInitJsonCommand.model_names, LOADJSON_DIR, str(tmp_path))
        assert trimmed is False
        assert notes == []
        # 无裁剪无冲突：直接用仓库里的种子文件（不产生临时文件）
        assert all(label.startswith(LOADJSON_DIR) for label in labels)
        assert not list(tmp_path.iterdir())

    def test_conflict_trims_seed_and_cascades(self, tmp_path):
        ApprovalFlow.objects.create(pk="cd81e9b1-f744-495d-b0ba-5d3b2273752a", code="demo_expense", name="库内对象")
        labels, notes, trimmed = build_seed_fixtures(LoadInitJsonCommand.model_names, LOADJSON_DIR, str(tmp_path))

        assert trimmed is True
        assert notes
        assert all(label.startswith(str(tmp_path)) for label in labels)
        flows = json.load(open(tmp_path / "approvalflow.json", encoding="utf-8"))
        codes = {row["fields"]["code"] for row in flows}
        assert "demo_expense" not in codes
        assert {"leave", "demo_leave"} <= codes

        nodes = json.load(open(tmp_path / "approvalflownode.json", encoding="utf-8"))
        original_nodes = _read("approvalflownode.json")
        assert 0 < len(nodes) < len(original_nodes)
        assert all(row["fields"]["flow"] != EXPENSE_SEED_PK for row in nodes)
        assert "approvalflowversion" in {os.path.basename(label).removesuffix(".json") for label in labels}
