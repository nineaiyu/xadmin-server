# -*- coding: utf-8 -*-
"""数据权限菜单上下文（显式 action 覆盖 + 同资源 list 回退）解析测试。"""

from types import SimpleNamespace

import pytest

from common.core.data_scope.menu_scope import (
    MenuScope,
    menu_binding_matches_context,
    resolve_menu_scope_pks,
    resolve_scope_menu_pks,
    scope_mode,
    select_compiled_grants,
)

pytestmark = pytest.mark.django_db


def _user(menu=None):
    return SimpleNamespace(menu=menu)


class TestResolveMenuScopePks:
    def test_permission_action_anchors_to_list(self, menu_factory):
        list_menu = menu_factory("list:Book", path="api/demo/book$", method="GET")
        preview_menu = menu_factory("preview:Book", path=r"api/demo/book/(?P<pk>[^/.]+)/preview$", method="GET")
        assert resolve_menu_scope_pks(preview_menu.pk) == frozenset({str(preview_menu.pk), str(list_menu.pk)})

    def test_page_resolves_to_descendant_list_permissions(self, menu_factory):
        page = menu_factory("BookPage", path="/demo/book/index", menu_type=1)
        list_menu = menu_factory("list:Book", path="api/demo/book$", method="GET", parent=page)
        menu_factory("preview:Book", path=r"api/demo/book/(?P<pk>[^/.]+)/preview$", method="GET", parent=page)
        assert resolve_menu_scope_pks(page.pk) == frozenset({str(list_menu.pk)})

    def test_directory_resolves_nested_list_permissions(self, menu_factory):
        directory = menu_factory("DemoDir", path="/demo", menu_type=0)
        page = menu_factory("BookPage", path="/demo/book/index", menu_type=1, parent=directory)
        list_menu = menu_factory("list:Book", path="api/demo/book$", method="GET", parent=page)
        assert resolve_menu_scope_pks(directory.pk) == frozenset({str(list_menu.pk)})

    def test_page_without_list_permission_has_empty_scope(self, menu_factory):
        page = menu_factory("EmptyPage", path="/demo/empty", menu_type=1)
        menu_factory("create:Book", path="api/demo/book$", method="POST", parent=page)
        assert resolve_menu_scope_pks(page.pk) == frozenset()


class TestMenuBindingMatchesContext:
    def test_list_binding_matches_sibling_action(self, menu_factory):
        list_menu = menu_factory("list:Book", path="api/demo/book$", method="GET")
        preview_menu = menu_factory("preview:Book", path=r"api/demo/book/(?P<pk>[^/.]+)/preview$", method="GET")
        assert menu_binding_matches_context(list_menu.pk, preview_menu.pk) is True
        assert menu_binding_matches_context(list_menu.pk, list_menu.pk) is True
        # 单向继承：只绑 preview 不反向放大 list
        assert menu_binding_matches_context(preview_menu.pk, list_menu.pk) is False

    def test_list_binding_matches_page_context(self, menu_factory):
        page = menu_factory("BookPage", path="/demo/book/index", menu_type=1)
        list_menu = menu_factory("list:Book", path="api/demo/book$", method="GET", parent=page)
        assert menu_binding_matches_context(list_menu.pk, page.pk) is True

    def test_page_binding_matches_descendant_action(self, menu_factory):
        page = menu_factory("BookPage", path="/demo/book/index", menu_type=1)
        preview_menu = menu_factory(
            "preview:Book", path=r"api/demo/book/(?P<pk>[^/.]+)/preview$", method="GET", parent=page
        )
        other_page = menu_factory("OtherPage", path="/demo/other/index", menu_type=1)
        assert menu_binding_matches_context(page.pk, preview_menu.pk) is True
        assert menu_binding_matches_context(page.pk, other_page.pk) is False

    def test_list_binding_does_not_match_other_resource(self, menu_factory):
        list_book = menu_factory("list:Book", path="api/demo/book$", method="GET")
        list_other = menu_factory("list:Other", path="api/demo/other$", method="GET")
        assert menu_binding_matches_context(list_book.pk, list_other.pk) is False


class TestResolveScopeMenuPks:
    def test_without_menu_context_returns_none(self):
        assert resolve_scope_menu_pks(_user()) is None

    def test_list_action_has_no_extra_anchor(self, menu_factory):
        menu = menu_factory("list:Book", path="api/demo/book$", method="GET")
        assert resolve_scope_menu_pks(_user(str(menu.pk))) == frozenset({str(menu.pk)})

    def test_child_action_anchors_to_list(self, menu_factory):
        list_menu = menu_factory("list:Book", path="api/demo/book$", method="GET")
        preview_menu = menu_factory("preview:Book", path=r"api/demo/book/(?P<pk>[^/.]+)/preview$", method="GET")
        assert resolve_scope_menu_pks(_user(str(preview_menu.pk))) == frozenset(
            {str(preview_menu.pk), str(list_menu.pk)}
        )

    def test_action_without_list_sibling_keeps_self(self, menu_factory):
        menu = menu_factory("preview:Standalone", path="api/demo/standalone/preview$", method="GET")
        assert resolve_scope_menu_pks(_user(str(menu.pk))) == frozenset({str(menu.pk)})

    def test_unknown_menu_pk_falls_back_to_self(self):
        menu_pk = "00000000-0000-0000-0000-000000000001"
        assert resolve_scope_menu_pks(_user(menu_pk)) == frozenset({menu_pk})

    def test_cache_recomputes_when_menu_context_changes(self, menu_factory):
        list_menu = menu_factory("list:Book", path="api/demo/book$", method="GET")
        other_list = menu_factory("list:Other", path="api/demo/other$", method="GET")
        user = _user(str(list_menu.pk))
        assert resolve_scope_menu_pks(user) == frozenset({str(list_menu.pk)})
        user.menu = str(other_list.pk)
        assert resolve_scope_menu_pks(user) == frozenset({str(other_list.pk)})


class TestSelectCompiledGrants:
    @staticmethod
    def _grant(menu_ids):
        return SimpleNamespace(menu_ids=frozenset(menu_ids))

    @staticmethod
    def _scope():
        return MenuScope(exact_pks=frozenset({"exact"}), fallback_pks=frozenset({"list"}))

    def test_exact_overrides_list(self):
        scope = self._scope()
        exact = self._grant({"exact"})
        listed = self._grant({"list"})
        generic = self._grant(set())
        compiled = [(exact, "exact-result"), (listed, "list-result"), (generic, "generic-result")]

        assert select_compiled_grants(compiled, scope) == [(exact, "exact-result"), (generic, "generic-result")]
        assert scope_mode(compiled, scope) == "exact"

    def test_list_fallback_when_no_exact(self):
        scope = self._scope()
        listed = self._grant({"list"})
        generic = self._grant(set())
        compiled = [(listed, "list-result"), (generic, "generic-result")]

        assert select_compiled_grants(compiled, scope) == compiled
        assert scope_mode(compiled, scope) == "list"

    def test_non_applicable_exact_does_not_block_fallback(self):
        scope = self._scope()
        exact = self._grant({"exact"})
        listed = self._grant({"list"})
        compiled = [(exact, None), (listed, "list-result")]

        assert select_compiled_grants(compiled, scope) == [(listed, "list-result")]
        assert scope_mode(compiled, scope) == "list"

    def test_no_fallback_keeps_generic_only(self):
        scope = MenuScope(exact_pks=frozenset({"exact"}))
        exact = self._grant({"exact"})
        generic = self._grant(set())
        compiled = [(exact, None), (generic, "generic-result")]

        assert select_compiled_grants(compiled, scope) == [(generic, "generic-result")]
        assert scope_mode(compiled, scope) == "generic"

    def test_without_scope_selects_all_applicable(self):
        first = self._grant({"a"})
        second = self._grant({"b"})
        compiled = [(first, "first"), (second, None)]

        assert select_compiled_grants(compiled, None) == [(first, "first")]
        assert scope_mode(compiled, None) == "generic"
