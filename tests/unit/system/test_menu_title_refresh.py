# -*- coding: utf-8 -*-
"""权限点标题重算：只修超限标题，保留人工短标题与 i18n key。"""

import pytest

import server.urls  # noqa: F401  # 预加载 urlconf，让 get_all_url_dict 的 import_string 生效
from system.models import Menu, MenuMeta
from system.services import permission_sync as sync

pytestmark = pytest.mark.django_db

LONG_TITLE = "文件（含分片上传 / 断点续传：chunk/* 子动作见 file_chunk.py）"


def _make_permission(name: str, path: str, method: str, title: str) -> Menu:
    meta = MenuMeta.objects.create(title=title)
    return Menu.objects.create(
        name=name,
        path=path,
        method=method,
        menu_type=Menu.MenuChoices.PERMISSION,
        meta=meta,
    )


class TestRefreshTitles:
    def test_refreshes_only_overlong_titles(self):
        overlong = _make_permission("list:UnitFile", "api/file/file$", "GET", LONG_TITLE)
        handmade = _make_permission("destroy:UnitFile", "api/file/file/(?P<pk>[^/.]+)$", "DELETE", "删文件")
        i18n_key = _make_permission("create:UnitFile", "api/file/file$", "POST", "menus.someLongTranslationKey")

        changed = sync.refresh_titles(sync.build_route_index(), [overlong, handmade, i18n_key])

        assert [menu.pk for menu, _old, _new in changed] == [overlong.pk]
        overlong.meta.refresh_from_db()
        handmade.meta.refresh_from_db()
        i18n_key.meta.refresh_from_db()
        assert overlong.meta.title == "获取文件的列表"
        assert handmade.meta.title == "删文件"
        assert i18n_key.meta.title == "menus.someLongTranslationKey"

    def test_dry_run_reports_without_writing(self):
        overlong = _make_permission("list:UnitFileDry", "api/file/file$", "GET", LONG_TITLE)

        changed = sync.refresh_titles(sync.build_route_index(), [overlong], dry_run=True)

        assert changed and changed[0][2] == "获取文件的列表"
        overlong.meta.refresh_from_db()
        assert overlong.meta.title == LONG_TITLE
