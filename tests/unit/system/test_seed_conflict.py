# -*- coding: utf-8 -*-
"""种子装配的冲突预检（system/services/seed.py）单元测试。

守护：库内数据优先——自然键被占用时跳过种子行并级联处理引用；无冲突时零改动
（直接用仓库原始种子文件）。

背景：loaddata 把整次导入放在单个事务里，一行冲突会回滚**全部** fixture（含菜单与
权限点）。历史现场：开箱模板命令按 code 重建了内置种子的示例流程，导致新装/升级时
重灌种子整体失败。
"""

import json
from pathlib import Path

import pytest

from approval.models.approval import ApprovalFlow, ApprovalFlowNode, ApprovalFlowVersion
from identity.models import UserRole
from system.management.commands.load_init_json import Command as LoadInitJsonCommand
from system.models import DataDict, ModelLabelField
from system.services.seed import _unique_checks, build_seed_fixtures, filter_conflicting_rows
from system.services.seeding import NOTE_CONFLICT, NOTE_PRUNE, read_model_rows

pytestmark = pytest.mark.django_db

EXPENSE_SEED_PK = "5eed0006-0000-4000-8000-000000000002"
LEAVE_SEED_PK = "5eed0006-0000-4000-8000-000000000003"
#: legacy 装配器按「一个模型一个文件」读单根目录，这里从分层种子还原该形态
LEGACY_FILES = {
    "approval.approvalflow": "approvalflow.json",
    "approval.approvalflownode": "approvalflownode.json",
    "approval.approvalflowversion": "approvalflowversion.json",
}


@pytest.fixture()
def legacy_root(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    for model_label, name in LEGACY_FILES.items():
        rows = read_model_rows(model_label, include_sample=True)
        (root / name).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return root


#: 测试读取口径（分层后按模型聚合）
READ_MODELS = {
    "userrole.json": "identity.userrole",
    "approvalflownode.json": "approval.approvalflownode",
}


def _read(name):
    return read_model_rows(READ_MODELS[name], include_sample=True)


class TestFilterConflictingRows:
    def test_no_conflict_keeps_rows(self):
        rows = {
            "approval.approvalflow": [
                {
                    "model": "approval.approvalflow",
                    "pk": EXPENSE_SEED_PK,
                    "fields": {"code": "brand_new", "name": "新流程"},
                }
            ]
        }
        filtered, notes = filter_conflicting_rows(rows)
        assert notes == []
        assert filtered["approval.approvalflow"] == rows["approval.approvalflow"]

    def test_same_pk_is_not_conflict(self):
        ApprovalFlow.objects.create(pk=EXPENSE_SEED_PK, code="demo_expense", name="同一条")
        rows = {
            "approval.approvalflow": [
                {
                    "model": "approval.approvalflow",
                    "pk": EXPENSE_SEED_PK,
                    "fields": {"code": "demo_expense", "name": "同一条"},
                }
            ]
        }
        filtered, notes = filter_conflicting_rows(rows)
        assert notes == []
        assert len(filtered["approval.approvalflow"]) == 1

    def test_conflicting_natural_key_dropped(self):
        ApprovalFlow.objects.create(pk="cd81e9b1-f744-495d-b0ba-5d3b2273752a", code="demo_expense", name="库内对象")
        rows = {
            "approval.approvalflow": [
                {
                    "model": "approval.approvalflow",
                    "pk": EXPENSE_SEED_PK,
                    "fields": {"code": "demo_expense", "name": "种子"},
                }
            ]
        }
        filtered, notes = filter_conflicting_rows(rows)
        assert filtered["approval.approvalflow"] == []
        assert len(notes) == 1
        assert "demo_expense" in notes[0] and "demo_expense" in notes[0]
        # 真冲突必须标记为 conflict：命令层据此保持 WARNING 并计入「跳过」数
        assert notes[0].kind == NOTE_CONFLICT

    def test_cascade_drops_referencing_rows(self):
        ApprovalFlow.objects.create(pk="cd81e9b1-f744-495d-b0ba-5d3b2273752a", code="demo_expense", name="库内对象")
        rows = {
            "approval.approvalflow": [
                {
                    "model": "approval.approvalflow",
                    "pk": EXPENSE_SEED_PK,
                    "fields": {"code": "demo_expense", "name": "种子"},
                },
                {
                    "model": "approval.approvalflow",
                    "pk": LEAVE_SEED_PK,
                    "fields": {"code": "leave", "name": "请假审批"},
                },
            ],
            "approval.approvalflownode": [
                {
                    "model": "approval.approvalflownode",
                    "pk": "node-a",
                    "fields": {"flow": EXPENSE_SEED_PK, "name": "节点A"},
                },
                {
                    "model": "approval.approvalflownode",
                    "pk": "node-b",
                    "fields": {"flow": LEAVE_SEED_PK, "name": "节点B"},
                },
            ],
        }
        filtered, notes = filter_conflicting_rows(rows)
        assert [row["pk"] for row in filtered["approval.approvalflow"]] == [LEAVE_SEED_PK]
        assert [row["pk"] for row in filtered["approval.approvalflownode"]] == ["node-b"]
        assert len(notes) == 2
        assert {note.kind for note in notes} == {NOTE_CONFLICT}

    def test_m2m_reference_trimmed(self):
        role = _read("userrole.json")[0]
        role_pk, role_code = role["pk"], role["fields"]["code"]
        UserRole.objects.create(pk="aaaa1111-0000-0000-0000-000000000000", code=role_code, name="库内角色")
        rows = {
            "identity.userrole": [
                {
                    "model": "identity.userrole",
                    "pk": role_pk,
                    "fields": {"code": role_code, "name": "种子角色", "menu": []},
                }
            ],
            "audit.datamaskrule": [
                {"model": "audit.datamaskrule", "pk": "rule-1", "fields": {"name": "规则", "roles": [role_pk]}}
            ],
        }
        filtered, notes = filter_conflicting_rows(rows)
        assert filtered["identity.userrole"] == []
        assert filtered["audit.datamaskrule"][0]["fields"]["roles"] == []
        assert any("datamaskrule" in note for note in notes)
        # 引用修剪（保留行内移除被跳过主键）与整行跳过是不同类别：
        # 两者都必须可见，但修剪不计入「跳过行数」
        prune_notes = [note for note in notes if "datamaskrule" in note]
        assert [note.kind for note in prune_notes] == [NOTE_PRUNE]
        assert [note.kind for note in notes if note not in prune_notes] == [NOTE_CONFLICT]

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
            "approval.approvalflownode": [
                {
                    "model": "approval.approvalflownode",
                    "pk": "5eed0007-0000-4000-8000-000000000005",
                    "fields": {"flow": LEAVE_SEED_PK, "order": 1, "name": "种子节点"},
                }
            ]
        }
        filtered, notes = filter_conflicting_rows(rows)
        assert filtered["approval.approvalflownode"] == []
        assert len(notes) == 1
        assert "flow=" in notes[0] and "order=1" in notes[0]
        assert notes[0].kind == NOTE_CONFLICT

    def test_composite_unique_same_pk_kept(self):
        ApprovalFlow.objects.create(pk=LEAVE_SEED_PK, code="leave", name="请假审批")
        node_pk = "5eed0007-0000-4000-8000-000000000005"
        ApprovalFlowNode.objects.create(pk=node_pk, flow_id=LEAVE_SEED_PK, order=1, name="同一条")
        rows = {
            "approval.approvalflownode": [
                {
                    "model": "approval.approvalflownode",
                    "pk": node_pk,
                    "fields": {"flow": LEAVE_SEED_PK, "order": 1, "name": "同一条"},
                }
            ]
        }
        filtered, notes = filter_conflicting_rows(rows)
        assert notes == []
        assert len(filtered["approval.approvalflownode"]) == 1

    def test_composite_unique_null_value_kept(self):
        """NULL 键只与 NULL 判冲突：库内同键取值非 NULL 时不误伤。"""
        ApprovalFlow.objects.create(pk=LEAVE_SEED_PK, code="leave", name="请假审批")
        ApprovalFlowNode.objects.create(
            pk="0ba28b97-24e2-4d34-a87a-787626fc5611", flow_id=LEAVE_SEED_PK, order=1, name="库内节点"
        )
        rows = {
            "approval.approvalflownode": [
                {
                    "model": "approval.approvalflownode",
                    "pk": "node-no-order",
                    "fields": {"flow": LEAVE_SEED_PK, "order": None, "name": "缺序节点"},
                }
            ]
        }
        filtered, notes = filter_conflicting_rows(rows)
        assert notes == []
        assert len(filtered["approval.approvalflownode"]) == 1

    def test_null_key_matches_db_null(self):
        """NULL 键与库内 NULL 视为同一取值：同名根节点换 pk 重现时按冲突跳过。

        现场背景：字段同步用新 pk 重建过 ``(name, parent=NULL)`` 的字段树根节点，
        种子行若不判冲突会与库内行共存成双根——PG 的 UNIQUE 对 NULL 互不相等，
        拦不住这种业务上的重复行（现场共 67 个根节点）。
        """
        ModelLabelField.objects.create(
            pk="dbce1001-0000-4000-8000-000000000001", name="identity.post", label="Post", field_type=1
        )
        rows = {
            "system.modellabelfield": [
                {
                    "model": "system.modellabelfield",
                    "pk": "dbce1002-0000-4000-8000-000000000002",
                    "fields": {"name": "identity.post", "parent": None, "label": "Post", "field_type": 1},
                }
            ]
        }
        filtered, notes = filter_conflicting_rows(rows)
        assert filtered["system.modellabelfield"] == []
        assert len(notes) == 1
        assert "name=identity.post" in notes[0]


class TestUniqueChecks:
    def test_composite_constraints_included(self):
        """组合唯一约束必须进入检查清单（单字段以外的约束不得被静默跳过）。"""
        assert ("flow", "order") in {names for names, _ in _unique_checks(ApprovalFlowNode)}
        assert ("flow", "version") in {names for names, _ in _unique_checks(ApprovalFlowVersion)}
        assert ("parent", "code") in {names for names, _ in _unique_checks(DataDict)}

    def test_unique_together_included(self):
        """老式 ``unique_together`` 同样进入检查清单（不在 _meta.constraints 里，漏判必崩）。

        字段标签树例外：ROLE / DATA 两棵树的模型根节点同名且 ``parent=NULL``，
        唯一键必须补上 ``field_type``，否则两棵树互相判重并级联剔除整棵子树。
        """
        assert {names for names, _ in _unique_checks(ModelLabelField)} == {("field_type", "name", "parent")}

    def test_modellabel_field_role_and_data_roots_are_independent(self):
        """ROLE 与 DATA 根节点同名不冲突；同 field_type 的真重复仍按冲突跳过。"""
        existing = ModelLabelField.objects.create(
            pk="dbce2001-0000-4000-8000-000000000001",
            name="identity.post",
            label="Post",
            field_type=ModelLabelField.FieldChoices.DATA,
        )
        role_row = {
            "model": "system.modellabelfield",
            "pk": "dbce2002-0000-4000-8000-000000000002",
            "fields": {
                "name": "identity.post",
                "parent": None,
                "label": "Post",
                "field_type": ModelLabelField.FieldChoices.ROLE,
            },
        }
        filtered, notes = filter_conflicting_rows({"system.modellabelfield": [role_row]})
        assert len(filtered["system.modellabelfield"]) == 1
        assert notes == []

        duplicate_data_row = {
            **role_row,
            "pk": "dbce2003-0000-4000-8000-000000000003",
            "fields": {**role_row["fields"], "field_type": ModelLabelField.FieldChoices.DATA},
        }
        filtered, notes = filter_conflicting_rows({"system.modellabelfield": [duplicate_data_row]})
        assert filtered["system.modellabelfield"] == []
        assert any(str(existing.pk) in str(note) for note in notes)


class TestBuildSeedFixtures:
    def test_clean_db_uses_original_files(self, tmp_path, legacy_root):
        labels, notes, trimmed, m2m = build_seed_fixtures(
            LoadInitJsonCommand.model_names, str(legacy_root), str(tmp_path / "out")
        )
        assert trimmed is False
        assert notes == []
        assert m2m == []
        # 无裁剪无冲突：直接用原始种子文件（不产生临时文件）
        assert all(label.startswith(str(legacy_root)) for label in labels)
        assert not (tmp_path / "out").exists()

    def test_conflict_trims_seed_and_cascades(self, tmp_path, legacy_root):
        ApprovalFlow.objects.create(pk="cd81e9b1-f744-495d-b0ba-5d3b2273752a", code="demo_expense", name="库内对象")
        out = tmp_path / "out"
        labels, notes, trimmed, m2m = build_seed_fixtures(LoadInitJsonCommand.model_names, str(legacy_root), str(out))

        assert trimmed is True
        assert notes
        assert all(label.startswith(str(out)) for label in labels)
        flows = json.load(open(out / "approvalflow.json", encoding="utf-8"))
        codes = {row["fields"]["code"] for row in flows}
        assert "demo_expense" not in codes
        assert {"leave", "demo_leave"} <= codes

        nodes = json.load(open(out / "approvalflownode.json", encoding="utf-8"))
        original_nodes = _read("approvalflownode.json")
        assert 0 < len(nodes) < len(original_nodes)
        assert all(row["fields"]["flow"] != EXPENSE_SEED_PK for row in nodes)
        assert "approvalflowversion" in {Path(label).stem for label in labels}
