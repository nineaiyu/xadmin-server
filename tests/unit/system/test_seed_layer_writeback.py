# -*- coding: utf-8 -*-
"""种子分层回写：规范文件名、同模型兄弟文件收敛、按归属落到应用层。

守护两条易漂移的口径：

1. 生成器早期按模型拆分的 ``menu_<model>.json`` 与分层工具的 ``menu.json`` 并存时，
   必须收敛到一个文件（否则同层同主键两份，``load_init_json --check`` 直接报错）；
2. ``sync_menu_permissions --update-seed`` 必须写到**所属应用**的种子目录，
   而不是一律塞回平台层（否则又要人工跑一次 ``seed_split``）。
"""

import json
from pathlib import Path

import pytest

from system.services.seeding import SeedManifest
from system.services.seeding.split import write_layer_model

MANIFEST = {"version": 1, "app": "crm", "depends": ["system"], "url_prefixes": ["api/crm/"], "files": []}


def _write(path: Path, rows: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def _rows(path: Path) -> list:
    return json.loads(path.read_text(encoding="utf-8"))


def _row(pk: str, name: str) -> dict:
    return {"model": "system.menu", "pk": pk, "fields": {"name": name, "menu_type": 2}}


class TestWriteLayerModel:
    def test_merge_absorbs_legacy_sibling_and_registers_canonical(self, tmp_path):
        seed_dir = tmp_path / "crm" / "seeds"
        manifest = dict(MANIFEST)
        manifest["files"] = [{"name": "menu_book.json", "model": "system.menu", "kind": "contract", "order": 20}]
        _write(seed_dir / "manifest.json", manifest)
        _write(seed_dir / "menu_book.json", [_row("pk-old", "list:Old")])

        write_layer_model(tmp_path, "crm", "system.menu", [_row("pk-new", "list:New")], merge=True)

        canonical = seed_dir / "menu.json"
        assert canonical.is_file()
        assert {row["pk"] for row in _rows(canonical)} == {"pk-old", "pk-new"}
        assert _rows(seed_dir / "menu_book.json") == []
        entries = json.loads((seed_dir / "manifest.json").read_text(encoding="utf-8"))["files"]
        assert [item["name"] for item in entries] == ["menu.json"]

    def test_merge_updates_same_pk_in_place(self, tmp_path):
        seed_dir = tmp_path / "crm" / "seeds"
        _write(seed_dir / "menu.json", [_row("pk-1", "list:Old"), _row("pk-2", "list:Keep")])

        write_layer_model(tmp_path, "crm", "system.menu", [_row("pk-1", "list:New")], merge=True)

        rows = _rows(seed_dir / "menu.json")
        assert [row["fields"]["name"] for row in rows] == ["list:New", "list:Keep"]

    def test_authoritative_write_drops_stale_sibling_rows(self, tmp_path):
        """导出/重排（``merge=False``）以库或文件全量为准：兄弟文件清空且不再登记。"""

        seed_dir = tmp_path / "crm" / "seeds"
        manifest = dict(MANIFEST)
        manifest["files"] = [{"name": "menu_legacy.json", "model": "system.menu", "kind": "contract", "order": 20}]
        seed_dir.mkdir(parents=True)
        (seed_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        _write(seed_dir / "menu_legacy.json", [_row("pk-old", "list:Old")])

        write_layer_model(tmp_path, "crm", "system.menu", [_row("pk-new", "list:New")])

        assert [row["pk"] for row in _rows(seed_dir / "menu.json")] == ["pk-new"]
        assert _rows(seed_dir / "menu_legacy.json") == []
        entries = json.loads((seed_dir / "manifest.json").read_text(encoding="utf-8"))["files"]
        assert [item["name"] for item in entries] == ["menu.json"]


@pytest.mark.django_db
class TestUpdateSeedWriteback:
    def test_write_seed_layers_targets_app_layer(self, settings, tmp_path):
        """按归属表写：应用行落 ``<app>/seeds/``，平台行落 ``loadjson/``。"""

        settings.PROJECT_DIR = str(tmp_path)
        manifests = {
            "crm": SeedManifest(app="crm", depends=("system",), url_prefixes=("api/crm/",)),
        }
        command = _command()
        entries = [
            {"model": "system.menu", "pk": "pk-app", "fields": {"name": "list:CrmCustomer", "menu_type": 2}},
            {"model": "system.menu", "pk": "pk-platform", "fields": {"name": "list:Menu", "menu_type": 2}},
        ]
        # 直接验证命令内部回写助手的落点（不经 --update-seed 的全量扫描）
        command._write_seed_layers({"pk-app": "crm", "pk-platform": "__platform__"}, "system.menu", entries, manifests)

        app_file = tmp_path / "crm" / "seeds" / "menu.json"
        platform_file = tmp_path / "loadjson" / "menu.json"
        assert [row["pk"] for row in _rows(app_file)] == ["pk-app"]
        assert [row["pk"] for row in _rows(platform_file)] == ["pk-platform"]
        app_manifest = json.loads((tmp_path / "crm" / "seeds" / "manifest.json").read_text(encoding="utf-8"))
        assert app_manifest["app"] == "crm"
        assert [item["name"] for item in app_manifest["files"]] == ["menu.json"]


def _command():
    from system.management.commands.sync_menu_permissions import Command

    return Command()
