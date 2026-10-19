# -*- coding: utf-8 -*-
"""种子引用完整性守护测试（``validate_plan`` 的 ``SEED_REF_MISSING``）。

拆分后同一个功能的数据会落在不同文件（页面与权限点、角色与菜单、白名单与字段树），
装载器必须在写库前发现「指向不存在对象」的引用，并定位到具体行——否则 ``loaddata``
只会抛一条没有上下文的 IntegrityError，且单事务回滚全部种子。
"""

import json
from pathlib import Path

from system.services.seeding import PLATFORM, SeedFileSpec, SeedPlan, validate_plan


def _spec(app: str, model: str, path: Path, rows: list[dict]) -> SeedFileSpec:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return SeedFileSpec(app=app, model=model, path=path)


def _menu(pk: str, *, parent: str | None = None, meta: str | None = None, models=()) -> dict:
    return {
        "model": "system.menu",
        "pk": pk,
        "fields": {"parent": parent, "meta": meta, "model": list(models), "menu_type": 1},
    }


def _meta(pk: str) -> dict:
    return {"model": "system.menumeta", "pk": pk, "fields": {"title": "x"}}


def _codes(result) -> list[str]:
    return [problem.code for problem in result.problems]


class TestReferenceIntegrity:
    def test_missing_foreign_key_is_reported(self, tmp_path):
        menu = _spec(
            PLATFORM,
            "system.menu",
            tmp_path / "menu.json",
            [_menu("page", parent="missing-parent")],
        )
        result = validate_plan(SeedPlan(platform=(menu,)))
        assert "SEED_REF_MISSING" in _codes(result)
        assert result.has_error()
        assert any("missing-parent" in problem.message for problem in result.problems)

    def test_missing_m2m_target_is_reported(self, tmp_path):
        meta = _spec(PLATFORM, "system.menumeta", tmp_path / "menumeta.json", [_meta("m1")])
        field = _spec(
            PLATFORM,
            "system.modellabelfield",
            tmp_path / "modellabelfield.json",
            [{"model": "system.modellabelfield", "pk": "f1", "fields": {"name": "identity.userinfo", "parent": None}}],
        )
        menu = _spec(
            PLATFORM,
            "system.menu",
            tmp_path / "menu.json",
            [_menu("page", meta="m1", models=("f1", "f-missing"))],
        )
        result = validate_plan(SeedPlan(platform=(meta, field, menu)))
        assert "SEED_REF_MISSING" in _codes(result)
        assert any("f-missing" in problem.message for problem in result.problems)

    def test_reference_to_non_seeded_model_is_ignored(self, tmp_path):
        """指向用户/部门等运行期数据的外键不参与校验（否则每行都是悬空）。"""

        menu = _spec(
            PLATFORM,
            "system.menu",
            tmp_path / "menu.json",
            [
                {
                    "model": "system.menu",
                    "pk": "page",
                    "fields": {"parent": None, "meta": None, "model": [], "creator": 1, "dept_belong": 1},
                }
            ],
        )
        result = validate_plan(SeedPlan(platform=(menu,)))
        assert "SEED_REF_MISSING" not in _codes(result)
        assert not result.has_error()

    def test_null_foreign_key_is_fine(self, tmp_path):
        menu = _spec(PLATFORM, "system.menu", tmp_path / "menu.json", [_menu("page", parent=None, meta=None)])
        result = validate_plan(SeedPlan(platform=(menu,)))
        assert not result.has_error()

    def test_cross_layer_reference_is_resolved_by_the_whole_plan(self, tmp_path):
        """平台层页面 + 应用层权限点：两者都在计划内即视为可解析。"""

        menu = _spec(
            PLATFORM,
            "system.menu",
            tmp_path / "platform" / "menu.json",
            [_menu("page")],
        )
        permission = _spec(
            "identity",
            "system.menu",
            tmp_path / "identity" / "menu.json",
            [{"model": "system.menu", "pk": "perm", "fields": {"parent": "page", "meta": None, "model": []}}],
        )
        result = validate_plan(SeedPlan(platform=(menu,), apps=("identity",), files={"identity": (permission,)}))
        assert "SEED_REF_MISSING" not in _codes(result)
        assert not result.has_error()

    def test_loading_one_app_without_its_parent_layer_fails_closed(self, tmp_path):
        """单独装某个应用但缺少其依赖时，必须在校验阶段就报错而不是留给 loaddata。"""

        permission = _spec(
            "identity",
            "system.menu",
            tmp_path / "identity" / "menu.json",
            [{"model": "system.menu", "pk": "perm", "fields": {"parent": "platform-page", "meta": None, "model": []}}],
        )
        result = validate_plan(SeedPlan(apps=("identity",), files={"identity": (permission,)}))
        assert "SEED_REF_MISSING" in _codes(result)
        assert any("platform-page" in problem.message for problem in result.problems)
