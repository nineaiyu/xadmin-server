# -*- coding: utf-8 -*-
"""种子装配说明的输出分流（``load_init_json``）。

背景：m2m 剥离（行照常导入）与唯一键冲突（整行跳过）曾共用「[种子冲突] /
共跳过 N 处」，新装环境也会打印 4 条 WARNING。本模块锁定两条口径：

1. 策略性剥离只作为普通提示，不冒充冲突、不计入「跳过」数；
2. 真冲突与引用修剪仍是 WARNING，且「跳过」只统计整行被丢弃的数量。
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.commands.loaddata import Command as LoadDataCommand

from system.management.commands import load_init_json as load_init_module
from system.management.commands.load_init_json import Command as LoadInitJsonCommand
from system.services.seeding import NOTE_CONFLICT, NOTE_PRUNE, NOTE_STRIP, LoadReport, SeedNote

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _restore_model_signal():
    """命令会把 ModelSignal.send 全局替换；测试后必须还原。"""

    from django.db.models.signals import ModelSignal

    original = ModelSignal.send
    yield
    ModelSignal.send = original


def _report() -> LoadReport:
    return LoadReport(
        notes=[
            SeedNote("跳过 approval.approvalflow(a)：code=demo 已被库内对象占用（pk=b）", NOTE_CONFLICT),
            SeedNote("audit.datamaskrule(c)：roles 移除 1 个已跳过主键", NOTE_PRUNE),
            SeedNote("identity.userrole(d)：剥离多对多字段 menu（改由声明式授权应用）", NOTE_STRIP),
        ],
        trimmed=True,
    )


def _run_command(monkeypatch, report: LoadReport, *, verbosity: int = 1) -> str:
    """只验证输出分流：替换装配与 loaddata，避免真实写库。"""

    out = StringIO()
    monkeypatch.setattr(load_init_module, "prepare", lambda *args, **kwargs: report)
    monkeypatch.setattr(LoadDataCommand, "handle", lambda self, *labels, **options: None)
    monkeypatch.setattr(LoadInitJsonCommand, "_apply_grants", lambda self, plan: None)
    monkeypatch.setattr(LoadInitJsonCommand, "_sync_builtin_roles_after_seed", lambda self: None)
    monkeypatch.setattr(LoadInitJsonCommand, "_invalidate_route_caches", lambda self: None)
    monkeypatch.setattr(load_init_module, "backfill_null_timestamps", lambda models: 0)
    monkeypatch.setattr(load_init_module, "invalid_dict_cache", lambda: None)
    call_command("load_init_json", stdout=out, verbosity=verbosity)
    return out.getvalue()


class TestNoteReporting:
    def test_strip_is_not_reported_as_conflict(self, monkeypatch):
        text = _run_command(monkeypatch, _report())

        assert "[种子装配] 剥离 1 处角色菜单授权字段" in text
        assert "[种子冲突] identity.userrole" not in text
        # 默认 verbosity=1 只给汇总，不逐条展开
        assert "剥离多对多字段 menu" not in text

    def test_real_conflicts_stay_warning_and_counted(self, monkeypatch):
        text = _run_command(monkeypatch, _report())

        assert "[种子冲突] 跳过 approval.approvalflow(a)" in text
        assert "[种子冲突] audit.datamaskrule(c)：roles 移除 1 个已跳过主键" in text
        # 只统计整行跳过；引用修剪单独计数，不被算成「跳过」
        assert "[种子冲突] 共跳过 1 行、修剪 1 处引用" in text
        assert "共跳过 3" not in text

    def test_strip_details_at_higher_verbosity(self, monkeypatch):
        text = _run_command(monkeypatch, _report(), verbosity=2)

        assert "[种子装配] identity.userrole(d)：剥离多对多字段 menu" in text

    def test_untyped_note_is_treated_as_conflict(self, monkeypatch):
        """未标类别的说明必须保持 WARNING——不能把潜在问题降级成提示。"""

        text = _run_command(monkeypatch, LoadReport(notes=["跳过 x：未标类别"], trimmed=True))

        assert "[种子冲突] 跳过 x：未标类别" in text
