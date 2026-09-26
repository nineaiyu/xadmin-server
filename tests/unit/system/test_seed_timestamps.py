# -*- coding: utf-8 -*-
"""内置种子时间戳回填守护（load_init_json 导入后回填 + 存量迁移）。

``loaddata`` 以 raw 方式保存对象（``save_base(raw=True)`` 跳过 ``pre_save``），
``auto_now_add``/``auto_now`` 不生效：种子 JSON 未显式给出时间的行落库为 NULL，
列表页「更新时间」列整列空白（dataset / screen / report / fieldpermission 等）。
``load_init_json`` 在导入后统一回填（只补 NULL 行）；存量库由数据迁移补齐。
"""

import pytest
from django.core.management import call_command
from django.core.management.commands.loaddata import Command as LoadDataCommand

from dataset.models import Dataset
from system.management.commands.load_init_json import Command as LoadInitJsonCommand
from system.utils.seed import backfill_null_timestamps

pytestmark = pytest.mark.django_db


class TestBackfillNullTimestamps:
    def test_fills_null_rows_and_keeps_existing(self):
        dataset = Dataset.objects.create(
            name="时间回填样例", bound_model="system.userinfo", columns=[], visibility="shared"
        )
        # 直连更新绕过 auto_now（模拟 loaddata raw 保存后的 NULL 行）
        Dataset.objects.filter(pk=dataset.pk).update(created_time=None, updated_time=None)
        assert backfill_null_timestamps([Dataset]) == 2
        dataset.refresh_from_db()
        assert dataset.created_time is not None
        assert dataset.updated_time is not None

        # 只补 NULL：已有时间的字段不被改写
        kept = dataset.updated_time
        Dataset.objects.filter(pk=dataset.pk).update(created_time=None)
        backfill_null_timestamps([Dataset])
        dataset.refresh_from_db()
        assert dataset.updated_time == kept
        assert dataset.created_time is not None

    def test_returns_zero_when_nothing_to_fill(self):
        Dataset.objects.create(name="时间完整样例", bound_model="system.userinfo", columns=[], visibility="shared")
        assert backfill_null_timestamps([Dataset]) == 0


class TestLoadInitJsonBackfillWiring:
    """命令接线：导入后必须调用回填（写库行为由父类 handle 承接，此处只验接线）。"""

    @pytest.fixture(autouse=True)
    def _restore_model_signal(self):
        """命令会把 ModelSignal.send 全局替换为忽略信号的实现，测试后必须还原。"""
        from django.db.models.signals import ModelSignal

        original = ModelSignal.send
        yield
        ModelSignal.send = original

    def test_command_calls_backfill(self, monkeypatch):
        import system.management.commands.load_init_json as command_module

        captured = {}

        def fake_handle(self, *labels, **options):
            return None

        def fake_backfill(model_names, **kwargs):
            captured["called"] = True
            captured["model_count"] = len(model_names)
            return 0

        monkeypatch.setattr(LoadDataCommand, "handle", fake_handle)
        monkeypatch.setattr(command_module, "backfill_null_timestamps", fake_backfill)
        call_command("load_init_json")

        assert captured.get("called") is True
        assert captured["model_count"] == len(LoadInitJsonCommand.model_names)
