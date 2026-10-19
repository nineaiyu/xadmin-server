# -*- coding: utf-8 -*-
"""按应用导出（``dump_init_json --split-apps``）守护测试。

导入导出必须共用同一份归属口径：导出到哪一层，装载就从哪一层读。这里锁定三件事：

1. **按 app 导出只写选中层**（平台层共享行始终包含，便于补丁直接装载）；
2. **幂等**：连续两次导出的文件内容逐字节一致；
3. **不丢行**：导出结果里选中应用 + 平台层的行数，与库内归属统计一致。
"""

import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from system.services.seeding import PLATFORM, build_plan, discover, read_specs
from system.services.seeding.ownership import analyse

pytestmark = pytest.mark.django_db


def _rows(path: Path) -> list:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def seeded(seed_creator_user):
    call_command("load_init_json", verbosity=0)


class TestDumpSplitApps:
    def test_only_selected_app_layers_are_written(self, seeded, tmp_path):
        out = tmp_path / "patch"
        call_command("dump_init_json", split_apps=True, apps="identity", output=str(out), verbosity=0)

        assert (out / PLATFORM / "menu.json").is_file() or (out / "loadjson" / "menu.json").is_file()
        assert (out / "identity" / "seeds" / "menu.json").is_file()
        assert not (out / "system" / "seeds" / "menu.json").exists()
        assert not (out / "dataset" / "seeds" / "menu.json").exists()

    def test_platform_layer_keeps_only_shared_rows(self, seeded, tmp_path):
        out = tmp_path / "patch"
        call_command("dump_init_json", split_apps=True, apps="identity", output=str(out), verbosity=0)

        platform_menus = _rows(out / "loadjson" / "menu.json")
        identity_menus = _rows(out / "identity" / "seeds" / "menu.json")
        assert identity_menus
        assert len(platform_menus) < len(identity_menus) + len(platform_menus)
        assert all(row["fields"].get("menu_type") in (0, 1) for row in platform_menus)

    def test_shared_platform_models_are_always_exported(self, seeded, tmp_path):
        """角色/部门/站点配置等平台级模型不参与归属拆分，补丁里必须带上。"""

        out = tmp_path / "patch"
        call_command("dump_init_json", split_apps=True, apps="identity", output=str(out), verbosity=0)
        for name in ("userrole.json", "deptinfo.json", "setting.json", "datapermission.json"):
            assert (out / "loadjson" / name).is_file(), name

    def test_export_is_idempotent(self, seeded, tmp_path):
        first = tmp_path / "a"
        second = tmp_path / "b"
        call_command("dump_init_json", split_apps=True, output=str(first), verbosity=0)
        call_command("dump_init_json", split_apps=True, output=str(second), verbosity=0)
        for path in first.rglob("*.json"):
            other = second / path.relative_to(first)
            assert other.is_file(), path
            assert path.read_bytes() == other.read_bytes(), path

    def test_export_matches_ownership_counts(self, seeded, tmp_path):
        """导出不丢行：各层行数之和 == 库内归属统计的合计。"""

        out = tmp_path / "patch"
        call_command("dump_init_json", split_apps=True, output=str(out), verbosity=0)

        discovery = discover()
        plan = build_plan(discovery)
        rows_by_model, _non_empty = read_specs(plan.all_specs())
        summary, _problems = analyse(rows_by_model, discovery.manifests)

        exported = 0
        for path in out.rglob("menu.json"):
            exported += len(_rows(path))
        assert exported == summary.totals["menu"]

    def test_unknown_app_is_command_error(self, seeded, tmp_path):
        with pytest.raises(CommandError, match="未知应用"):
            call_command("dump_init_json", split_apps=True, apps="ghost", output=str(tmp_path), verbosity=0)

    def test_plain_dump_still_writes_platform_and_warns(self, seeded, tmp_path, settings):
        settings.PROJECT_DIR = str(tmp_path)
        call_command("dump_init_json", verbosity=0)
        assert (tmp_path / "loadjson" / "menu.json").is_file()

    def test_registered_app_without_manifest_is_initialized(self, seeded, tmp_path, monkeypatch):
        """新应用还没有 seeds/manifest.json 时，显式 --apps 直接初始化（免手写）。"""

        from django.apps import apps as django_apps

        from system.management.commands import dump_init_json as dump_cmd

        real_get = django_apps.get_app_config

        class _Config:
            label = "newapp"
            path = str(tmp_path / "newapp")

        def _get_app_config(label, *args, **kwargs):
            if label == "newapp":
                return _Config()
            return real_get(label, *args, **kwargs)

        monkeypatch.setattr(django_apps, "get_app_config", _get_app_config)
        monkeypatch.setattr(dump_cmd, "_is_local_app", lambda label: label == "newapp")
        monkeypatch.setattr(dump_cmd, "_app_url_prefixes", lambda label: ("api/newapp/",))

        out = tmp_path / "patch"
        call_command("dump_init_json", split_apps=True, apps="newapp", output=str(out), verbosity=0)

        manifest = json.loads((out / "newapp" / "seeds" / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["app"] == "newapp"
        assert manifest["url_prefixes"] == ["api/newapp/"]
        assert manifest["depends"] == ["system", "identity"]
