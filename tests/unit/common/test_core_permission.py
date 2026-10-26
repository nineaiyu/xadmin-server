# -*- coding: utf-8 -*-
"""packages/xadmin-common/common/core/permission.py 菜单/接口权限 与 数据权限范围（HTTP 级）单元测试。

创建菜单权限 + 数据权限规则 + 字段权限，验证普通用户访问受保护接口的行为。
注意：权限结果按用户以 24h 缓存（MagicCacheData），因此"无权限->403"和
"有权限->200"必须在不同的测试方法中验证，以免同方法内缓存污染。
"""

import pytest

from demo.models import Book
from system.models import DataPermission, FieldPermission, ModelLabelField

pytestmark = pytest.mark.django_db

BOOK_LIST_URL = "/api/demo/book"


@pytest.fixture
def upload_file(superuser):
    from file.models import UploadFile

    return UploadFile.objects.create(
        filename="cover.png", filesize=100, mime_type="image/png", md5sum="a" * 32, creator=superuser
    )


def grant_list_permission(role, menu_factory):
    menu = menu_factory("p-list", path="api/demo/book$", method="GET")
    role.menu.add(menu)
    return menu


class TestMenuPermissionHttp:
    def test_unauthenticated_returns_401(self, api_client):
        resp = api_client.get(BOOK_LIST_URL)
        assert resp.status_code == 401
        assert resp.data["code"] == 401

    def test_with_menu_permission_returns_200(self, api_client, normal_user, role, menu_factory):
        """普通用户绑定菜单权限后可访问接口（未配置数据权限时结果为空但仍 200）。"""
        grant_list_permission(role, menu_factory)
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(BOOK_LIST_URL)
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert resp.data["data"]["total"] == 0

    def test_without_menu_permission_returns_403(self, api_client, normal_user):
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(BOOK_LIST_URL)
        assert resp.status_code == 403
        assert resp.data["code"] == 403

    def test_method_mismatch_denied(self, api_client, normal_user, role, menu_factory):
        """仅授予 GET 菜单权限，POST 请求应被拒绝。"""
        grant_list_permission(role, menu_factory)
        api_client.force_authenticate(user=normal_user)
        resp = api_client.post(BOOK_LIST_URL, {}, format="json")
        assert resp.status_code == 403
        assert resp.data["code"] == 403


class TestDataPermissionHttpScope:
    def test_user_sees_only_own_records(self, api_client, normal_user, role, menu_factory, superuser, upload_file):
        """接口权限 + 数据权限 + 字段权限联动：普通用户只能看到自己名下的书籍。"""
        menu = grant_list_permission(role, menu_factory)

        dp = DataPermission.objects.create(
            name="own",
            rules=[{"table": "demo.book", "field": "admin", "type": "value.user.id", "value": "*", "match": "exact"}],
        )
        normal_user.rules.add(dp)

        model_field = ModelLabelField.objects.create(
            name="demo.book", label="书籍", field_type=ModelLabelField.FieldChoices.ROLE
        )
        children = [
            ModelLabelField.objects.create(
                name=n, label=n, parent=model_field, field_type=ModelLabelField.FieldChoices.ROLE
            )
            for n in ["pk", "name", "isbn"]
        ]
        fp = FieldPermission.objects.create(role=role, menu=menu)
        fp.field.add(*children)

        Book.objects.create(name="别人的书", isbn="i1", author="a", admin=superuser, admin2=superuser, file=upload_file)
        my_book = Book.objects.create(
            name="我的书", isbn="i2", author="a", admin=normal_user, admin2=normal_user, file=upload_file
        )

        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(BOOK_LIST_URL)
        assert resp.status_code == 200
        assert resp.data["data"]["total"] == 1
        results = resp.data["data"]["results"]
        assert results[0]["pk"] == my_book.pk
        # 字段权限裁剪：仅暴露授权的 pk/name/isbn
        assert set(results[0].keys()) == {"pk", "name", "isbn"}


class TestSharedPathPermissionSelection:
    """同一 path+method 多权限码：主权限点确定、字段权限取并集。"""

    @staticmethod
    def _grant(role, *menus):
        from django.core.cache import cache as django_cache

        role.menu.add(*menus)
        django_cache.clear()

    def test_primary_code_uses_explicit_priority(self, normal_user, role, menu_factory):
        from common.core.permission import get_user_permission

        lower = menu_factory("retrieve:SettingWatermark", path="api/settings/basic$", method="GET")
        higher = menu_factory("retrieve:SettingBasic", path="api/settings/basic$", method="GET")
        # 故意先加低优先级码：主权限点选择不得依赖创建/查询顺序
        self._grant(role, lower, higher)

        data = get_user_permission(normal_user, "GET")
        assert data["api/settings/basic$"][0] == higher.pk

    def test_menus_returns_every_held_code(self, normal_user, role, menu_factory):
        from common.core.permission import get_user_permission_menus

        menus = [
            menu_factory(name, path="api/audit/logs/operation$", method="GET")
            for name in ("list:SystemOperationLog", "changeHistory:SystemUser", "changeHistory:SystemDept")
        ]
        self._grant(role, *menus)

        entries = get_user_permission_menus(normal_user, "GET")["api/audit/logs/operation$"]
        assert {name for _pk, name, _model in entries} == {
            "list:SystemOperationLog",
            "changeHistory:SystemUser",
            "changeHistory:SystemDept",
        }

    def test_field_permissions_are_union_of_shared_codes(self, normal_user, role, menu_factory):
        from common.core.permission import get_user_field_queryset_for_menus
        from system.models import FieldPermission, ModelLabelField

        basic = menu_factory("retrieve:SettingBasic", path="api/settings/basic$", method="GET")
        watermark = menu_factory("retrieve:SettingWatermark", path="api/settings/basic$", method="GET")
        self._grant(role, basic, watermark)

        parent = ModelLabelField.objects.create(
            name="demo.book", label="书籍", field_type=ModelLabelField.FieldChoices.ROLE
        )
        name_field = ModelLabelField.objects.create(
            name="name", label="名称", parent=parent, field_type=ModelLabelField.FieldChoices.ROLE
        )
        isbn_field = ModelLabelField.objects.create(
            name="isbn", label="ISBN", parent=parent, field_type=ModelLabelField.FieldChoices.ROLE
        )
        FieldPermission.objects.create(role=role, menu=basic).field.add(name_field)
        FieldPermission.objects.create(role=role, menu=watermark).field.add(isbn_field)

        merged = get_user_field_queryset_for_menus(normal_user, [basic.pk, watermark.pk])
        assert merged["demo.book"] == {"name", "isbn"}


def test_every_duplicate_seed_permission_is_registered():
    """所有 seeds 中重复的 (path, method) 必须在视图声明处登记，且与兜底表一致。

    运行时按 path 建字典只能保留一个权限点；未登记的共享端点会退化为按码名排序，
    字段权限上下文与数据权限菜单上下文都不可预期。新增共享权限点必须在视图上用
    ``@shared_permissions`` 声明（含该端点下的全部权限码）；兼容期兜底表
    ``SHARED_PERMISSION_PRIORITY`` 仍保留，但必须与声明注册表完全一致。
    """
    import json
    from collections import defaultdict
    from pathlib import Path

    from django.conf import settings

    # 注册表由视图模块导入时填充：显式导入四组声明方，避免依赖测试执行顺序
    from audit.views.admin.operationlog import OperationLogViewSet  # noqa: F401
    from common.core.permission import SHARED_PERMISSION_PRIORITY
    from common.core.permission_meta import SHARED_PERMISSION_REGISTRY, normalize_shared_path
    from settings.views.basic import BasicSettingViewSet  # noqa: F401
    from task.views.task import TaskExecutionViewSet  # noqa: F401

    root = Path(settings.PROJECT_DIR)
    ignored_parts = {".venv", "node_modules", "_delete", "dist"}
    seed_files = [path for path in root.rglob("seeds/menu.json") if not ignored_parts & set(path.parts)]
    seed_files.append(root / "loadjson/menu.json")

    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for path in seed_files:
        if not path.exists():
            continue
        for row in json.loads(path.read_text(encoding="utf-8")):
            fields = row.get("fields", {})
            if fields.get("menu_type") == 2 and fields.get("is_active", True):
                groups[(str(fields.get("path")), str(fields.get("method")))].append(str(fields.get("name")))
    duplicates = {key: names for key, names in groups.items() if len(names) > 1}

    unregistered = set(duplicates) - set(SHARED_PERMISSION_PRIORITY)
    assert not unregistered, f"共享权限点未登记 SHARED_PERMISSION_PRIORITY: {sorted(unregistered)}"
    stale = set(SHARED_PERMISSION_PRIORITY) - set(duplicates)
    assert not stale, f"SHARED_PERMISSION_PRIORITY 存在失效登记（seeds 已无重复码）: {sorted(stale)}"
    for key, names in duplicates.items():
        assert set(names) == set(SHARED_PERMISSION_PRIORITY[key]), (
            f"{key} 的权限码未完整登记：seeds={sorted(names)} priority={sorted(SHARED_PERMISSION_PRIORITY[key])}"
        )

    declared = {
        (normalize_shared_path(path), method.upper()): spec
        for (path, method), spec in SHARED_PERMISSION_REGISTRY.items()
    }
    missing_views = {(normalize_shared_path(path), method.upper()) for path, method in duplicates} - set(declared)
    assert not missing_views, f"共享权限点未在视图声明处登记: {sorted(missing_views)}"
    for key, names in duplicates.items():
        normalized = (normalize_shared_path(key[0]), key[1].upper())
        spec = declared[normalized]
        assert set(spec.priority) == set(names), (
            f"{key} 的视图声明未覆盖全部权限码：seeds={sorted(names)} declared={sorted(spec.priority)}"
        )
        assert spec.priority == SHARED_PERMISSION_PRIORITY[key], (
            f"{key} 的视图声明与兜底表顺序不一致：declared={spec.priority} table={SHARED_PERMISSION_PRIORITY[key]}"
        )
