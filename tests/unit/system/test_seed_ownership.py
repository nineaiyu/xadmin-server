# -*- coding: utf-8 -*-
"""种子归属规则守护测试（``system/services/seeding/ownership.py``）。

归属是「按 app 拆分种子」的前提：每一行必须能判给某个 app 或平台层，且判定必须可复现、
可解释（前缀最长匹配 / 页面众数 / 字段树根前缀 / 清单声明）。这里锁定规则的边界与
未判定项的呈现方式，避免规则悄悄退化成「猜」。
"""

import json
from pathlib import Path

from system.services.seeding import (
    PLATFORM,
    SeedFileSpec,
    SeedManifest,
    SeedPlan,
    analyse_ownership,
    build_plan,
    discover,
    validate_plan,
)

DIRECTORY = 0
PAGE = 1
PERMISSION = 2


def _manifest(app: str, *, prefixes=(), config_keys=(), dict_codes=()) -> SeedManifest:
    return SeedManifest(
        app=app, url_prefixes=tuple(prefixes), config_keys=tuple(config_keys), dict_codes=tuple(dict_codes)
    )


def _menu(pk: str, menu_type: int, name: str, *, path: str = "", parent: str | None = None) -> dict:
    return {
        "model": "system.menu",
        "pk": pk,
        "fields": {"menu_type": menu_type, "name": name, "path": path, "parent": parent},
    }


def _field(pk: str, name: str, *, parent: str | None = None) -> dict:
    return {"model": "system.modellabelfield", "pk": pk, "fields": {"name": name, "parent": parent, "field_type": 1}}


def _grant(pk: str, menu_pk: str) -> dict:
    return {"model": "system.fieldpermission", "pk": pk, "fields": {"menu": menu_pk}}


def _config(pk: str, key: str) -> dict:
    return {"model": "system.systemconfig", "pk": pk, "fields": {"key": key}}


def _codes(problems) -> list[str]:
    return [problem.code for problem in problems]


class TestPermissionOwnership:
    def test_longest_prefix_wins(self):
        manifests = {
            "system": _manifest("system", prefixes=["api/system/"]),
            "ai": _manifest("ai", prefixes=["api/system/ai/"]),
        }
        rows = {("system.menu"): [_menu("p1", PERMISSION, "ask:Ai", path="api/system/ai/assistant/ask$")]}
        summary, problems = analyse_ownership(rows, manifests)
        assert problems == []
        assert summary.by_app["ai"]["menu"] == 1
        assert "system" not in summary.by_app

    def test_trailing_slash_and_dollar_are_normalized(self):
        manifests = {"system": _manifest("system", prefixes=["/api/system"])}
        rows = {"system.menu": [_menu("p1", PERMISSION, "list:X", path="/api/system/users$")]}
        summary, problems = analyse_ownership(rows, manifests)
        assert problems == []
        assert summary.by_app["system"]["menu"] == 1

    def test_uncovered_prefix_is_error(self):
        manifests = {"system": _manifest("system", prefixes=["api/system/"])}
        rows = {"system.menu": [_menu("p1", PERMISSION, "list:X", path="api/unknown/thing$")]}
        summary, problems = analyse_ownership(rows, manifests)
        assert "OWNERSHIP_PREFIX_UNCOVERED" in _codes(problems)
        assert summary.permission_total == 1
        assert summary.by_app == {}


class TestPageOwnership:
    def test_single_app_page_goes_to_that_app(self):
        manifests = {"identity": _manifest("identity", prefixes=["api/identity/"])}
        rows = {
            "system.menu": [
                _menu("page", PAGE, "SystemUser"),
                _menu("p1", PERMISSION, "list:SystemUser", path="api/identity/user$", parent="page"),
                _menu("p2", PERMISSION, "create:SystemUser", path="api/identity/user$", parent="page"),
                _menu("p3", PERMISSION, "update:SystemUser", path="api/identity/user$", parent="page"),
                _menu("p4", PERMISSION, "del:SystemUser", path="api/identity/user$", parent="page"),
                _menu("p5", PERMISSION, "history:SystemUser", path="api/identity/user$", parent="page"),
            ]
        }
        summary, problems = analyse_ownership(rows, manifests)
        assert problems == []
        assert summary.by_app["identity"]["menu"] == 6

    def test_mixed_page_stays_on_platform(self):
        manifests = {
            "identity": _manifest("identity", prefixes=["api/identity/"]),
            "audit": _manifest("audit", prefixes=["api/audit/"]),
        }
        rows = {
            "system.menu": [
                _menu("page", PAGE, "UserInfo"),
                _menu("p1", PERMISSION, "a", path="api/identity/a$", parent="page"),
                _menu("p2", PERMISSION, "b", path="api/identity/b$", parent="page"),
                _menu("p3", PERMISSION, "c", path="api/audit/c$", parent="page"),
                _menu("p4", PERMISSION, "d", path="api/audit/d$", parent="page"),
                _menu("p5", PERMISSION, "e", path="api/audit/e$", parent="page"),
            ]
        }
        summary, problems = analyse_ownership(rows, manifests)
        assert "OWNERSHIP_MIXED_PAGE" in _codes(problems)
        assert summary.mixed_pages == ("UserInfo",)
        assert summary.by_app[PLATFORM]["menu"] == 1
        assert summary.by_app["identity"]["menu"] == 2
        assert summary.by_app["audit"]["menu"] == 3

    def test_directory_always_platform(self):
        manifests = {"system": _manifest("system", prefixes=["api/system/"])}
        rows = {"system.menu": [_menu("d", DIRECTORY, "system", path="/system")]}
        summary, _problems = analyse_ownership(rows, manifests)
        assert summary.by_app[PLATFORM]["menu"] == 1


class TestFieldOwnership:
    def test_child_follows_root_app(self):
        manifests = {"identity": _manifest("identity")}
        rows = {
            "system.modellabelfield": [
                _field("root", "identity.userinfo"),
                _field("child", "username", parent="root"),
                _field("grand", "dept", parent="child"),
            ]
        }
        summary, problems = analyse_ownership(rows, manifests)
        assert problems == []
        assert summary.by_app["identity"]["field"] == 3

    def test_public_fields_and_unlisted_apps_go_to_platform(self):
        manifests = {"identity": _manifest("identity")}
        rows = {
            "system.modellabelfield": [
                _field("all", "*"),
                _field("all_fields", "*", parent="all"),
                _field("celery", "django_celery_beat.periodictask"),
                _field("ghost", "ghostapp.model"),
            ]
        }
        summary, problems = analyse_ownership(rows, manifests)
        assert summary.by_app[PLATFORM]["field"] == 4
        # 三方 app 静默归平台层；未声明清单的业务 app 给出一次聚合提示
        assert summary.undeclared_apps == ("ghostapp",)
        assert "OWNERSHIP_APP_UNDECLARED" in _codes(problems)


class TestGrantOwnership:
    def test_grant_follows_its_menu(self):
        manifests = {"identity": _manifest("identity", prefixes=["api/identity/"])}
        rows = {
            "system.menu": [_menu("p1", PERMISSION, "list:X", path="api/identity/x$")],
            "system.fieldpermission": [_grant("g1", "p1")],
        }
        summary, problems = analyse_ownership(rows, manifests)
        assert problems == []
        assert summary.by_app["identity"]["grant"] == 1

    def test_orphan_grant_is_error(self):
        manifests = {"identity": _manifest("identity")}
        rows = {"system.fieldpermission": [_grant("g1", "missing")]}
        _summary, problems = analyse_ownership(rows, manifests)
        assert "GRANT_MENU_MISSING" in _codes(problems)


class TestConfigAndDict:
    def test_declared_config_key_follows_app(self):
        manifests = {"file": _manifest("file", config_keys=["FILE_UPLOAD_SIZE"])}
        rows = {"system.systemconfig": [_config("c1", "FILE_UPLOAD_SIZE")]}
        summary, problems = analyse_ownership(rows, manifests)
        assert problems == []
        assert summary.by_app["file"]["config"] == 1

    def test_undeclared_config_key_defaults_to_platform_with_aggregate_warning(self):
        manifests = {"file": _manifest("file")}
        rows = {"system.systemconfig": [_config("c1", "UNKNOWN_KEY"), _config("c2", "UNKNOWN_KEY")]}
        summary, problems = analyse_ownership(rows, manifests)
        assert summary.by_app[PLATFORM]["config"] == 2
        assert summary.undeclared_config == ("UNKNOWN_KEY",)
        assert _codes(problems).count("CONFIG_UNOWNED") == 1

    def test_duplicate_declaration_is_error(self):
        manifests = {
            "file": _manifest("file", config_keys=["FILE_UPLOAD_SIZE"]),
            "system": _manifest("system", config_keys=["FILE_UPLOAD_SIZE"]),
        }
        result = validate_plan(SeedPlan(apps=("file", "system")), manifests=manifests)
        assert "OWNERSHIP_DECLARATION_CONFLICT" in _codes(result.problems)
        assert result.has_error()

    def test_declared_but_absent_key_is_warning(self, tmp_path: Path):
        path = tmp_path / "systemconfig.json"
        path.write_text(json.dumps([_config("c1", "REAL_KEY")]), encoding="utf-8")
        spec = SeedFileSpec(app=PLATFORM, model="system.systemconfig", path=path)
        manifests = {"file": _manifest("file", config_keys=["TYPO_KEY"])}
        result = validate_plan(SeedPlan(platform=(spec,)), manifests=manifests)
        assert "OWNERSHIP_DECLARATION_UNKNOWN" in _codes(result.problems)
        assert not result.has_error()


class TestDictOwnership:
    """字典按**根分组**归属：同一分组不能拆到两个层（子项 code 与根 code 不同）。"""

    def _rows_with_layer(self):
        from system.services.seeding import build_plan, discover

        plan = build_plan(discover())
        placed: dict[str, str] = {}
        rows: list[dict] = []
        for spec in plan.all_specs():
            if spec.model != "system.datadict" or not spec.path.is_file():
                continue
            for row in json.loads(spec.path.read_text(encoding="utf-8")):
                rows.append(row)
                placed[row["pk"]] = spec.app
        return rows, placed

    def test_every_group_stays_in_one_layer(self):
        rows, placed = self._rows_with_layer()
        assert rows
        by_pk = {row["pk"]: row for row in rows}

        def root_pk(row):
            current = row
            seen = set()
            while current["fields"].get("parent"):
                parent = current["fields"]["parent"]
                if parent in seen or parent not in by_pk:
                    break
                seen.add(parent)
                current = by_pk[parent]
            return current["pk"]

        group_layers: dict[str, set[str]] = {}
        for row in rows:
            group_layers.setdefault(root_pk(row), set()).add(placed[row["pk"]])
        split = {pk for pk, layers in group_layers.items() if len(layers) > 1}
        assert not split, f"字典分组被拆到多个层：{sorted(split)[:3]}"

    def test_only_cross_domain_group_stays_on_platform(self):
        """login_type 同时被 identity（会话/在线）与 audit（登录日志）消费 → 留平台层。"""

        discovery = discover()
        result = validate_plan(build_plan(discovery), manifests=discovery.manifests)
        summary = result.ownership
        assert summary is not None
        assert summary.undeclared_dict == ("login_type",)
        assert summary.by_app[PLATFORM]["dict"] == 1
        assert summary.totals["dict"] == 55
