# -*- coding: utf-8 -*-
"""模块裁剪下的种子过滤守护测试（`load_init_json`）。

新装库的形态必须与运行期裁剪口径一致：配置了停用模块时，种子里的菜单 /
菜单元数据 / 字段权限 / 角色菜单绑定中的对应条目不入库；未配置时零行为差异。
"""

import json
import os
import tempfile

import pytest
from django.conf import settings as dj_settings
from django.core.management import call_command
from django.core.management.commands.loaddata import Command as LoadDataCommand

from common.core.modules import ModuleSeedFilter, compute_hidden_menu_pks
from system.management.commands.load_init_json import Command as LoadInitJsonCommand
from system.models import FieldPermission, Menu, MenuMeta, UserRole
from system.utils.seed import build_seed_fixtures

LOADJSON_DIR = os.path.join(dj_settings.PROJECT_DIR, "loadjson")
TRIMMED_MODULES = ("chat", "analysis")

# 种子装配（build_seed_fixtures）要查库判定自然键冲突，故整模块需要数据库
pytestmark = pytest.mark.django_db


def _read(name):
    with open(os.path.join(LOADJSON_DIR, name), encoding="utf-8") as fp:
        return json.load(fp)


def _export(seed_filter, models, target_dir):
    """把过滤结果落到 target_dir，返回 {文件名: rows}。"""

    build_seed_fixtures(models, LOADJSON_DIR, str(target_dir), module_filter=seed_filter)
    return {
        model._meta.model_name: json.load(open(target_dir / f"{model._meta.model_name}.json", encoding="utf-8"))
        for model in models
    }


class TestComputeHiddenMenuPks:
    """纯函数口径（运行期与种子共用）。"""

    ROWS = [
        ("dir", None, 0, "integration", "/integration"),
        ("menu_a", "dir", 1, "WebhookSubscription", "/integration/subscription/index"),
        ("perm_a", "menu_a", 2, "list:WebhookSubscription", "api/system/webhooks/subscriptions$"),
        ("menu_b", "dir", 1, "IntegrationApiApp", "/integration/api-app/index"),
        ("perm_search", "root", 2, "retrieve:SystemGlobalSearch", "api/system/global-search$"),
        ("root", None, 0, "system", "/system"),
        ("menu_core", "root", 1, "SystemUser", "/system/user/index"),
    ]

    def test_subtree_hidden(self):
        hidden = compute_hidden_menu_pks(self.ROWS, names={"WebhookSubscription"}, prefixes=())
        assert hidden == frozenset({"menu_a", "perm_a"})

    def test_empty_directory_pruned(self):
        hidden = compute_hidden_menu_pks(self.ROWS, names={"WebhookSubscription", "IntegrationApiApp"}, prefixes=())
        assert "dir" in hidden
        assert "root" not in hidden

    def test_permission_prefix_hidden(self):
        hidden = compute_hidden_menu_pks(self.ROWS, names=(), prefixes=("api/system/global-search",))
        assert hidden == frozenset({"perm_search"})

    def test_no_rule_returns_empty(self):
        assert compute_hidden_menu_pks(self.ROWS, names=(), prefixes=()) == frozenset()


class TestSeedFilterOnRealFiles:
    """直接跑真实 loadjson（最贴近新装行为）。"""

    def test_full_preset_needs_no_filter(self):
        assert ModuleSeedFilter.build() is None

    def test_disabled_module_menu_and_meta_removed(self, module_config, tmp_path):
        module_config(disable=["chat"])
        seed_filter = ModuleSeedFilter.build()
        # 与命令的真实顺序一致：menumeta 排在 menu 之前（顺序敏感，历史缺陷已修）
        exported = _export(seed_filter, [MenuMeta, Menu], tmp_path)

        original_menus = {row["pk"]: row["fields"] for row in _read("menu.json")}
        original_metas = {row["pk"] for row in _read("menumeta.json")}
        filtered_menus = {row["pk"]: row["fields"] for row in exported["menu"]}
        filtered_metas = {row["pk"] for row in exported["menumeta"]}

        chat_pk = next(pk for pk, fields in original_menus.items() if fields["name"] == "Chat")
        assert chat_pk in seed_filter.hidden
        assert chat_pk not in filtered_menus

        # 只被剔除菜单引用的 meta 一并剔除；其余 meta（含原有孤儿）原样保留
        referenced = {fields["meta"] for fields in original_menus.values() if fields.get("meta")}
        kept_referenced = {
            fields["meta"]
            for pk, fields in original_menus.items()
            if pk not in seed_filter.hidden and fields.get("meta")
        }
        assert original_menus[chat_pk]["meta"] in referenced - kept_referenced
        assert original_metas - filtered_metas == referenced - kept_referenced
        system_user_pk = next(pk for pk, fields in original_menus.items() if fields["name"] == "SystemUser")
        assert system_user_pk in filtered_menus

    def test_emptied_directories_removed_from_real_seed(self, module_config, tmp_path):
        """子模块全停用后，空目录（数据分析/集成/表单采集）一并剔除。

        回归：`compute_hidden_menu_pks` 早期实现只遍历一次 rows，种子侧传入生成器时
        目录修剪失效（顶层目录会以"空分组"形态留在新装库里）。
        """
        module_config(disable=["analysis", "ai", "webhook", "open_platform", "dform"])
        seed_filter = ModuleSeedFilter.build()
        exported = _export(seed_filter, [Menu], tmp_path)

        names = {row["fields"]["name"] for row in exported["menu"]}
        for name in ("dataAnalysis", "integration", "formCollection"):
            assert name not in names
        assert "SystemUser" in names

    def test_role_menu_binding_trimmed(self, module_config, tmp_path):
        module_config(disable=list(TRIMMED_MODULES))
        seed_filter = ModuleSeedFilter.build()
        exported = _export(seed_filter, [Menu, UserRole], tmp_path)

        original = {row["pk"]: row["fields"]["menu"] for row in _read("userrole.json")}
        removed_total = 0
        for row in exported["userrole"]:
            bound = row["fields"]["menu"]
            assert not (set(bound) & seed_filter.hidden)
            removed_total += len(original[row["pk"]]) - len(bound)
        assert removed_total > 0, "停用模块的菜单绑定应被移除"
        assert all(row["fields"]["menu"] for row in exported["userrole"])

    def test_field_permission_rows_dropped(self, module_config, tmp_path):
        # 字段权限绑定在菜单（权限点行）上：全局搜索模块的 9 条绑定随模块停用被剔除
        module_config(disable=["search"])
        seed_filter = ModuleSeedFilter.build()
        exported = _export(seed_filter, [Menu, FieldPermission], tmp_path)

        rows = exported["fieldpermission"]
        assert rows, "过滤后仍应保留内核页面的字段权限"
        assert all(row["fields"]["menu"] not in seed_filter.hidden for row in rows)
        assert len(_read("fieldpermission.json")) - len(rows) > 0

    def test_filtered_seed_referential_integrity(self, module_config, tmp_path):
        """过滤后仍是一套自洽的种子（loaddata 不会撞外键）。

        测试库不跑 load_init_json（项目既有约定：命令会全局改写 ModelSignal.send
        并写库，污染同进程后续用例），因此这里在数据层面校验全部外键可达。
        """
        module_config(
            disable=["chat", "analysis", "search", "webhook", "dform", "approval_flow", "ai", "open_platform"]
        )
        seed_filter = ModuleSeedFilter.build()
        models = LoadInitJsonCommand.model_names
        exported = _export(seed_filter, models, tmp_path)

        def pks(model_name):
            return {row["pk"] for row in exported.get(model_name, [])}

        menu_pks = pks("menu")
        meta_pks = pks("menumeta")
        label_field_pks = pks("modellabelfield")
        for row in exported["menu"]:
            fields = row["fields"]
            assert fields.get("parent") in (None, *menu_pks), f"菜单父级缺失: {fields.get('name')}"
            assert fields.get("meta") in meta_pks, f"菜单 meta 缺失: {fields.get('name')}"
            assert set(fields.get("model") or []) <= label_field_pks, f"菜单模型绑定缺失: {fields.get('name')}"
        for row in exported["fieldpermission"]:
            fields = row["fields"]
            assert fields["menu"] in menu_pks, "字段权限指向了被剔除的菜单"
            assert set(fields.get("field") or []) <= label_field_pks
        for model_name, key in (("userrole", "menu"), ("datapermission", "menu")):
            for row in exported.get(model_name, []):
                assert set(row["fields"].get(key) or []) <= menu_pks, f"{model_name}.{key} 仍引用被剔除菜单"


@pytest.mark.django_db
class TestLoadInitJsonCommandWiring:
    """命令接线：配置停用模块时把「过滤后的临时种子」交给 loaddata。

    只验证接线（父类 handle 被替换为捕获入参的空实现）：命令写库/改写全局信号
    的行为不在此处执行，避免污染同进程后续用例（测试库不跑真实 load_init_json）。
    """

    @pytest.fixture(autouse=True)
    def _restore_model_signal(self):
        """命令会把 ModelSignal.send 全局替换为忽略信号的实现，测试后必须还原。

        否则同进程后续依赖模型信号（权限缓存失效、任务执行记录）的用例会静默失效。
        """
        from django.db.models.signals import ModelSignal

        original = ModelSignal.send
        yield
        ModelSignal.send = original

    def test_labels_point_to_filtered_fixtures(self, module_config, monkeypatch):
        captured = {}

        def fake_handle(self, *labels, **options):
            # 临时目录在命令退出后即清理，内容必须在调用期读取
            captured["labels"] = labels
            menu_label = next(label for label in labels if label.endswith("menu.json"))
            with open(menu_label, encoding="utf-8") as fp:
                captured["menu_names"] = {row["fields"]["name"] for row in json.load(fp)}

        monkeypatch.setattr(LoadDataCommand, "handle", fake_handle)
        module_config(disable=["chat"])
        call_command("load_init_json")

        labels = captured["labels"]
        assert len(labels) == len(LoadInitJsonCommand.model_names)
        # 传的是过滤后的临时文件（不再是 loadjson/ 原始路径）
        assert all(label.startswith(tempfile.gettempdir()) for label in labels)
        assert "Chat" not in captured["menu_names"]
        assert "SystemUser" in captured["menu_names"]
        assert not os.path.exists(labels[0]), "临时目录应随命令结束清理"

    def test_full_preset_passes_raw_fixtures(self, module_config, monkeypatch):
        captured = {}

        def fake_handle(self, *labels, **options):
            captured["labels"] = labels

        monkeypatch.setattr(LoadDataCommand, "handle", fake_handle)
        module_config()
        call_command("load_init_json")

        assert all(label.startswith(LOADJSON_DIR) for label in captured["labels"])
