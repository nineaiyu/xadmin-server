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
from identity.models import UserRole
from settings.models import Setting
from system.management.commands.load_init_json import Command as LoadInitJsonCommand
from system.models import FieldPermission, Menu, MenuMeta, SystemConfig
from system.services.seed import write_seed_rows
from system.services.seeding import build_fixtures, build_plan, discover, read_model_rows

LOADJSON_DIR = os.path.join(dj_settings.PROJECT_DIR, "loadjson")
TRIMMED_MODULES = ("chat", "analysis")


def _all_specs():
    """各层（平台 + 应用）的种子文件清单：拆分后同一模型可能来自多个文件。"""

    return build_plan(discover()).all_specs()


def _non_empty_specs():
    return [spec for spec in _all_specs() if spec.path.is_file() and json.loads(spec.path.read_text(encoding="utf-8"))]


# 种子装配（build_seed_fixtures）要查库判定自然键冲突，故整模块需要数据库
pytestmark = pytest.mark.django_db


def _read(name):
    with open(os.path.join(LOADJSON_DIR, name), encoding="utf-8") as fp:
        return json.load(fp)


def _export(seed_filter, models, target_dir):
    """把过滤结果落到 target_dir，返回 {模型: rows}（空种子不落盘，视作空）。"""

    wanted = {model._meta.label_lower for model in models}
    build_fixtures([spec for spec in _all_specs() if spec.model in wanted], str(target_dir), module_filter=seed_filter)
    exported = {}
    for model in models:
        path = target_dir / f"{model._meta.model_name}.json"
        exported[model._meta.model_name] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    return exported


class TestComputeHiddenMenuPks:
    """纯函数口径（运行期与种子共用）。"""

    ROWS = [
        ("dir", None, 0, "integration", "/integration"),
        ("menu_a", "dir", 1, "WebhookSubscription", "/integration/subscription/index"),
        ("perm_a", "menu_a", 2, "list:WebhookSubscription", "api/task/webhooks/subscriptions$"),
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

        original_menus = {row["pk"]: row["fields"] for row in read_model_rows("system.menu")}
        original_metas = {row["pk"] for row in read_model_rows("system.menumeta")}
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

    def test_role_menu_binding_moved_to_grants(self, module_config, tmp_path):
        """角色菜单授权已改为 grants 声明 + 装载期并集，不再随 userrole.json 裁剪。

        停用模块的菜单不入库，``apply_grants`` 按名查找时自然跳过——与「从绑定列表里
        移除」等价，且不会误删其它层声明的授权（覆盖式 m2m 无法表达这一点）。
        """
        module_config(disable=list(TRIMMED_MODULES))
        seed_filter = ModuleSeedFilter.build()
        exported = _export(seed_filter, [Menu, UserRole], tmp_path)

        # 种子角色行不再携带 m2m 绑定（装配层统一剥离，空数组同样会触发 set([]) 清零）
        assert all(not row["fields"].get("menu") for row in exported["userrole"])
        # 菜单侧仍按模块裁剪：被隐藏的菜单不再出现在导出结果里
        hidden_names = {
            row["fields"]["name"] for row in read_model_rows("system.menu") if row["pk"] in seed_filter.hidden
        }
        kept_names = {row["fields"]["name"] for row in exported["menu"]}
        assert hidden_names & kept_names == set()

    def test_field_permission_rows_dropped(self, module_config, tmp_path):
        # 字段权限绑定在菜单（权限点行）上：全局搜索模块的 9 条绑定随模块停用被剔除
        module_config(disable=["search"])
        seed_filter = ModuleSeedFilter.build()
        exported = _export(seed_filter, [Menu, FieldPermission], tmp_path)

        rows = exported["fieldpermission"]
        assert rows, "过滤后仍应保留内核页面的字段权限"
        assert all(row["fields"]["menu"] not in seed_filter.hidden for row in rows)
        assert len(read_model_rows("system.fieldpermission")) - len(rows) > 0

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
        # 空种子模型（无行）不进清单：loaddata 对空 fixture 会告警且无可导入内容
        assert len(labels) == len({spec.model for spec in _non_empty_specs()})
        # 传的是过滤后的临时文件（不再是 loadjson/ 原始路径）
        assert all(label.startswith(tempfile.gettempdir()) for label in labels)
        assert "Chat" not in captured["menu_names"]
        assert "SystemUser" in captured["menu_names"]
        assert not os.path.exists(labels[0]), "临时目录应随命令结束清理"

    def test_full_preset_strips_m2m_then_loads(self, module_config, monkeypatch):
        """全量预设：授权型 m2m 被剥离，fixture 里不再出现 ``menu`` 键。

        种子 ``userrole.json`` 携带 ``menu`` 键（哪怕为空数组），``loaddata`` 会
        ``set([])`` 清零存量授权——装配层必须剥离它。
        """

        captured = {}

        def fake_handle(self, *labels, **options):
            captured["labels"] = [str(label) for label in labels]
            for label in captured["labels"]:
                if not label.endswith("userrole.json"):
                    continue
                with open(label, encoding="utf-8") as fp:
                    captured["roles"] = json.load(fp)

        monkeypatch.setattr(LoadDataCommand, "handle", fake_handle)
        module_config()
        call_command("load_init_json")

        assert captured["labels"], "应仍有待导入的种子文件"
        # 剥离后的角色行不携带授权字段
        assert all("menu" not in row["fields"] for row in captured["roles"])


class TestEmptySeedFilesSkipped:
    """空种子文件不进待导入清单（覆盖装配的两条分支）。

    loaddata 对空 fixture 会报 "No fixture data found for '<model>'"（RuntimeWarning
    噪音）且没有可导入内容——跳过与导入空文件语义等价。
    """

    def test_labels_exclude_empty_seeds(self, module_config, tmp_path):
        module_config()  # 授权型 m2m 需剥离：同样走落盘分支（不再是原始文件路径）
        labels, _, trimmed, _m2m = build_fixtures(_all_specs(), str(tmp_path))
        assert trimmed
        assert labels
        assert len(labels) == len({spec.model for spec in _non_empty_specs()})

        module_config(disable=["chat"])  # 有裁剪：过滤后落盘分支（按模型合并）
        labels, _, trimmed, _m2m = build_fixtures(_all_specs(), str(tmp_path), module_filter=ModuleSeedFilter.build())
        assert trimmed
        assert labels
        for label in labels:
            with open(label, encoding="utf-8") as fp:
                assert json.load(fp), f"空种子不应进清单: {os.path.basename(label)}"

    def test_write_seed_rows_skips_empty_models(self, tmp_path):
        row = {"model": "system.systemconfig", "pk": "1", "fields": {"key": "X", "value": {}}}
        labels = write_seed_rows({SystemConfig._meta.label_lower: [row]}, [SystemConfig, Setting], str(tmp_path))
        assert [os.path.basename(label) for label in labels] == ["systemconfig.json"]
        assert not (tmp_path / "setting.json").exists()
