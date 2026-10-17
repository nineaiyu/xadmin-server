# -*- coding: utf-8 -*-
"""种子发现 / 规划 / 校验的守护测试（``system/services/seeding``）。

锁定三件事：

1. **零行为变化**：未迁移任何 app 时，平台层装载顺序必须与拆分前的历史模型序完全一致；
2. **计划可计算**：依赖闭包、拓扑排序、成环与未知应用都要有确定结论，不依赖目录遍历顺序；
3. **问题可定位**：主键重复 / 模型未注册 / 清单写错都带文件与行号，且 ``--list`` / ``--check`` 不写库。
"""

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.commands.loaddata import Command as LoadDataCommand

from system.management.commands.load_init_json import Command as LoadInitJsonCommand
from system.services.seeding import (
    PLATFORM,
    Discovery,
    SeedFileSpec,
    SeedManifest,
    SeedPlan,
    SeedPlanError,
    build_plan,
    discover,
    parse_manifest,
    platform_dir,
    read_specs,
    validate_plan,
)


def _manifest(app: str, *, depends=(), files=()) -> SeedManifest:
    return SeedManifest(app=app, depends=tuple(depends), files=tuple(files))


def _spec(app: str, model: str, path: Path, *, order: int = 10, kind: str = "contract") -> SeedFileSpec:
    return SeedFileSpec(app=app, model=model, path=path, kind=kind, order=order)


def _write(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


class TestPlatformPlan:
    """平台层顺序即历史顺序：这是「拆分不改变任何行为」的基线。"""

    def test_plan_covers_every_legacy_model(self):
        """拆分后模型分散在各层，但覆盖的模型集合必须与历史清单完全一致。"""

        plan = build_plan(discover())
        expected = [model._meta.label_lower for model in LoadInitJsonCommand.model_names]
        assert sorted(plan.model_labels()) == sorted(expected)
        assert len(plan.model_labels()) == len(expected)

    def test_layer_internal_order_follows_legacy(self):
        """每层内部的模型顺序必须遵循历史拓扑序（menumeta → menu → … → modellabelfield）。"""

        plan = build_plan(discover())
        legacy = [model._meta.label_lower for model in LoadInitJsonCommand.model_names]
        for specs in (plan.platform, *plan.files.values()):
            indices = [legacy.index(spec.model) for spec in specs]
            assert indices == sorted(indices), [spec.model for spec in specs]

    def test_platform_files_live_under_loadjson(self):
        plan = build_plan(discover())
        assert plan.platform
        assert all(spec.path.parent == platform_dir() for spec in plan.platform)

    def test_manifest_declares_only_platform_owned_files(self):
        raw = json.loads((platform_dir() / "manifest.json").read_text(encoding="utf-8"))
        assert raw["app"] == PLATFORM
        assert {item["name"] for item in raw["files"]} <= {spec.name for spec in build_plan(discover()).platform}


class TestPlanComposition:
    """依赖闭包与顺序计算（不依赖真实文件，纯结构）。"""

    def _discovery(self) -> Discovery:
        manifests = {
            "alpha": _manifest("alpha", depends=("beta",), files=[_spec("alpha", "x.alpha", Path("/tmp/a.json"))]),
            "beta": _manifest("beta", files=[_spec("beta", "x.beta", Path("/tmp/b.json"))]),
            "gamma": _manifest("gamma", files=[_spec("gamma", "x.gamma", Path("/tmp/g.json"))]),
        }
        return Discovery(platform=SeedManifest(app=PLATFORM), manifests=manifests)

    def test_dependency_closure_adds_upstream_and_orders_it_first(self):
        plan = build_plan(self._discovery(), apps=["alpha"])
        assert plan.apps == ("beta", "alpha")
        assert any("按依赖补充装载" in note for note in plan.notes)

    def test_unselected_apps_are_excluded(self):
        plan = build_plan(self._discovery(), apps=["gamma"])
        assert plan.apps == ("gamma",)
        assert "alpha" not in plan.files

    def test_unknown_app_fails_closed(self):
        with pytest.raises(SeedPlanError, match="未知应用"):
            build_plan(self._discovery(), apps=["missing"])

    def test_missing_dependency_fails_closed(self):
        discovery = Discovery(
            platform=SeedManifest(app=PLATFORM),
            manifests={"alpha": _manifest("alpha", depends=("ghost",))},
        )
        with pytest.raises(SeedPlanError, match="seeds/manifest.json"):
            build_plan(discovery, apps=["alpha"])

    def test_cycle_fails_closed(self):
        discovery = Discovery(
            platform=SeedManifest(app=PLATFORM),
            manifests={
                "alpha": _manifest("alpha", depends=("beta",)),
                "beta": _manifest("beta", depends=("alpha",)),
            },
        )
        with pytest.raises(SeedPlanError, match="成环"):
            build_plan(discovery, apps=["alpha"])

    def test_sample_files_are_excluded_by_default(self):
        discovery = Discovery(
            platform=SeedManifest(app=PLATFORM),
            manifests={
                "alpha": SeedManifest(
                    app="alpha",
                    files=(_spec("alpha", "x.alpha", Path("/tmp/a.json")),),
                    sample=(_spec("alpha", "x.alpha", Path("/tmp/sample/a.json"), kind="sample"),),
                )
            },
        )
        assert build_plan(discovery, apps=["alpha"]).samples == ()
        assert len(build_plan(discovery, apps=["alpha"], include_sample=True).samples) == 1


class TestManifestParsing:
    def test_valid_manifest(self, tmp_path):
        path = _write(
            tmp_path / "manifest.json",
            {
                "version": 1,
                "app": "alpha",
                "depends": ["system"],
                "url_prefixes": ["api/alpha/"],
                "files": [{"name": "menu.json", "model": "system.menu", "order": 20}],
            },
        )
        manifest, problems = parse_manifest(path, expected_app="alpha")
        assert manifest is not None and manifest.files[0].name == "menu.json"
        assert problems == []

    def test_app_mismatch_is_reported(self, tmp_path):
        path = _write(tmp_path / "manifest.json", {"version": 1, "app": "beta", "files": []})
        _manifest_obj, problems = parse_manifest(path, expected_app="alpha")
        assert any(problem.code == "MANIFEST_APP_MISMATCH" for problem in problems)

    def test_unknown_kind_is_reported(self, tmp_path):
        path = _write(
            tmp_path / "manifest.json",
            {"version": 1, "app": "alpha", "files": [{"name": "menu.json", "model": "system.menu", "kind": "weird"}]},
        )
        _manifest_obj, problems = parse_manifest(path, expected_app="alpha")
        assert any(problem.code == "MANIFEST_INVALID" and "kind" in problem.message for problem in problems)

    def test_sample_outside_sample_dir_is_reported(self, tmp_path):
        path = _write(
            tmp_path / "manifest.json",
            {"version": 1, "app": "alpha", "sample": [{"name": "demo.json", "model": "system.menu"}]},
        )
        _manifest_obj, problems = parse_manifest(path, expected_app="alpha")
        assert any("sample/" in problem.message for problem in problems)

    def test_path_traversal_is_rejected(self, tmp_path):
        path = _write(
            tmp_path / "manifest.json",
            {"version": 1, "app": "alpha", "files": [{"name": "../menu.json", "model": "system.menu"}]},
        )
        _manifest_obj, problems = parse_manifest(path, expected_app="alpha")
        assert any(problem.code == "MANIFEST_INVALID" for problem in problems)


class TestValidate:
    def test_duplicate_pk_in_same_layer_is_error(self, tmp_path):
        row = {"model": "system.menu", "pk": "same", "fields": {}}
        first = _spec("alpha", "system.menu", _write(tmp_path / "a" / "menu.json", [row]))
        second = _spec("alpha", "system.menu", _write(tmp_path / "a" / "menu2.json", [row]))
        result = validate_plan(SeedPlan(apps=("alpha",), files={"alpha": (first, second)}))
        assert any(problem.code == "SEED_PK_DUPLICATE" for problem in result.problems)
        assert result.has_error()

    def test_duplicate_pk_across_layers_is_warning(self, tmp_path):
        row = {"model": "system.menu", "pk": "same", "fields": {}}
        platform = _spec(PLATFORM, "system.menu", _write(tmp_path / "menu.json", [row]))
        app = _spec("alpha", "system.menu", _write(tmp_path / "app" / "menu.json", [row]))
        result = validate_plan(SeedPlan(platform=(platform,), apps=("alpha",), files={"alpha": (app,)}))
        assert [problem.code for problem in result.problems] == ["SEED_PK_OVERRIDE"]
        assert not result.has_error()

    def test_model_mismatch_is_reported(self, tmp_path):
        spec = _spec(
            "alpha", "system.menu", _write(tmp_path / "menu.json", [{"model": "system.x", "pk": "1", "fields": {}}])
        )
        result = validate_plan(SeedPlan(apps=("alpha",), files={"alpha": (spec,)}))
        assert any(problem.code == "SEED_MODEL_MISMATCH" for problem in result.problems)

    def test_unknown_model_is_reported(self, tmp_path):
        spec = _spec("alpha", "nope.nothing", _write(tmp_path / "x.json", []))
        result = validate_plan(SeedPlan(apps=("alpha",), files={"alpha": (spec,)}))
        assert any(problem.code == "SEED_MODEL_UNKNOWN" for problem in result.problems)

    def test_missing_file_is_reported(self, tmp_path):
        result = validate_plan(
            SeedPlan(apps=("alpha",), files={"alpha": (_spec("alpha", "system.menu", tmp_path / "none.json"),)})
        )
        assert any(problem.code == "SEED_FILE_MISSING" for problem in result.problems)

    def test_shipped_seed_files_are_clean(self):
        """仓库内种子必须通过结构 + 归属校验（进 CI 的硬门禁）。"""

        discovery = discover()
        result = validate_plan(build_plan(discovery), manifests=discovery.manifests)
        assert [problem for problem in result.problems if problem.level == "error"] == []
        assert result.ownership is not None
        assert not any(problem.code == "OWNERSHIP_PREFIX_UNCOVERED" for problem in result.problems)

    def test_shipped_seeds_cover_every_menu_and_field_row(self):
        """归属必须是全量的：菜单与字段树的每一行都要有明确归属。"""

        discovery = discover()
        result = validate_plan(build_plan(discovery), manifests=discovery.manifests)
        summary = result.ownership
        assert summary is not None
        assert summary.totals["menu"] == 714
        assert summary.totals["field"] == 2094
        assert summary.totals["grant"] == 253
        assert summary.permission_total == 639


class TestRows:
    def test_same_model_from_two_layers_merges_in_load_order(self, tmp_path):
        platform = _spec(PLATFORM, "system.menu", _write(tmp_path / "menu.json", [{"pk": "a"}, {"pk": "b"}]))
        app = _spec("alpha", "system.menu", _write(tmp_path / "app" / "menu.json", [{"pk": "a"}]))
        rows, non_empty = read_specs([platform, app])
        assert [row["pk"] for row in rows["system.menu"]] == ["a", "b", "a"]
        assert len(non_empty) == 2


class TestCommandWiring:
    def test_list_prints_map_without_loading(self, monkeypatch):
        called = {}

        def fake_handle(self, *labels, **options):
            called["load"] = True

        monkeypatch.setattr(LoadDataCommand, "handle", fake_handle)
        out = StringIO()
        call_command("load_init_json", list=True, stdout=out)

        assert "装载计划" in out.getvalue()
        assert "load" not in called

    def test_check_passes_on_shipped_seeds(self):
        out = StringIO()
        call_command("load_init_json", check=True, stdout=out)
        assert "种子校验通过" in out.getvalue()

    def test_check_does_not_load(self, monkeypatch):
        called = {}

        def fake_handle(self, *labels, **options):
            called["load"] = True

        monkeypatch.setattr(LoadDataCommand, "handle", fake_handle)
        call_command("load_init_json", check=True, stdout=StringIO())
        assert "load" not in called

    def test_unknown_app_is_command_error(self):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError, match="未知应用"):
            call_command("load_init_json", apps="ghost", stdout=StringIO())
