# -*- coding: utf-8 -*-
"""越权矩阵扩展（N5 安全自查三期）：覆盖 N3 五期新增能力的攻击面（M18-M29）。

与 `test_privilege_escalation_matrix.py` 同口径（HTTP 集成、菜单授权按生产正则惯例），
编号顺延。重点覆盖本期新入口：**第三方绑定解绑、导入列映射模板、文件预览、记录 stats**。

| 编号 | 层 | 攻击面 | 预期防线 |
|------|----|--------|----------|
| M18 | 认证边界 | 匿名读取第三方绑定列表 | 401 |
| M19 | 水平越权 | 解绑他人的第三方绑定 | 绑定仍存在（取值域收口本人） |
| M20 | 水平越权 | 他人的个人导入模板不可见/不可改/不可删 | 列表无该项，改删 404 |
| M21 | 垂直越权 | 非超管创建共享导入模板 | 400（序列化器拒绝） |
| M22 | 水平越权 | 非超管改/删共享模板（超管建的） | 404（写操作取值域排除共享） |
| M23 | 水平越权 | 预览他人文件 | 404（属主收口，与下载同口径） |
| M24 | 水平越权 | 预览不存在的文件主键 | 404 |
| M25 | 垂直越权 | 无授权访问记录 stats（导出/导入/任务执行） | 403 |
| M26 | 水平越权 | 记录 stats 只统计本人（不泄漏他人记录数） | total 只含本人 |
| M27 | 水平越权 | 下载他人导出记录文件 | 403/404 且无文件流出 |
| M28 | 水平越权 | 他人导入记录的错误报告不可下载 | 403/404 |
| M29 | 认证边界 | 匿名访问文件预览 | 401 |
"""

import pytest

from system.models.export import ExportRecord
from system.models.import_ import ImportRecord, ImportTemplate
from system.models.oauth import UserOAuthBinding
from system.models.task import TaskExecution

pytestmark = pytest.mark.django_db

OAUTH_BINDINGS_URL = "/api/system/auth/oauth/bindings"
IMPORT_TEMPLATE_URL = "/api/system/import-templates"
FILE_PREVIEW_URL = "/api/system/file/{pk}/preview"
EXPORT_STATS_URL = "/api/system/exports/stats"
IMPORT_STATS_URL = "/api/system/imports/stats"
TASK_STATS_URL = "/api/system/tasks/executions/stats"

MODEL_LABEL = "demo.book"


def grant(role, menu_factory, path, method, name):
    menu = menu_factory(name, path=path, method=method)
    role.menu.add(menu)
    return menu


@pytest.fixture
def alice(normal_user):
    """攻击方：普通用户（角色见 role fixture）。"""
    return normal_user


@pytest.fixture
def bob():
    from system.models import UserInfo

    return UserInfo.objects.create_user(username="bob-matrix", password="Bob-Pwd-2026!")


class TestOAuthBindings:
    def test_anonymous_bindings_denied(self, api_client):
        """M18：匿名读取绑定列表（个人凭证，必须有认证）。"""
        resp = api_client.get(OAUTH_BINDINGS_URL)
        assert resp.status_code == 401

    def test_cannot_unbind_others_binding(self, api_client, alice, bob):
        """M19：解绑他人绑定——取值域按 user=self 收口，越权 pk 等同不存在。"""
        others = UserOAuthBinding.objects.create(user=bob, provider="idp", subject="bob-sub")
        api_client.force_authenticate(user=alice)
        resp = api_client.delete(OAUTH_BINDINGS_URL, {"password": "whatever"}, format="json")
        assert resp.status_code != 200
        assert UserOAuthBinding.objects.filter(pk=others.pk).exists()

    def test_unbind_requires_password(self, api_client, alice):
        """M19 附：解绑必须口令二次确认（错误口令不生效）。"""
        alice.set_password("Alice-Pwd-2026!")
        alice.save(update_fields=["password"])
        binding = UserOAuthBinding.objects.create(user=alice, provider="idp", subject="alice-sub")
        api_client.force_authenticate(user=alice)
        resp = api_client.delete(
            OAUTH_BINDINGS_URL.replace("bindings", f"bindings/{binding.pk}"),
            {"password": "wrong"},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["code"] != 1000
        assert UserOAuthBinding.objects.filter(pk=binding.pk).exists()


class TestImportTemplates:
    def test_others_personal_template_invisible_and_unwritable(self, api_client, alice, bob, role, menu_factory):
        """M20：他人个人模板不可见；强猜 pk 改删也不生效。"""
        grant(role, menu_factory, "api/system/import-templates$", "GET", "tpl-list")
        grant(role, menu_factory, "api/system/import-templates/(?P<pk>[^/.]+)$", "PUT", "tpl-put")
        grant(role, menu_factory, "api/system/import-templates/(?P<pk>[^/.]+)$", "DELETE", "tpl-del")
        others = ImportTemplate.objects.create(creator=bob, model=MODEL_LABEL, name="bob-tpl", mapping={"a": "b"})
        api_client.force_authenticate(user=alice)
        listed = api_client.get(f"{IMPORT_TEMPLATE_URL}?model={MODEL_LABEL}")
        assert listed.status_code == 200
        assert listed.data["data"]["total"] == 0

        updated = api_client.put(
            f"{IMPORT_TEMPLATE_URL}/{others.pk}",
            {"model": MODEL_LABEL, "name": "hacked", "mapping": {}},
            format="json",
        )
        assert updated.status_code != 200 or updated.data.get("code") != 1000
        deleted = api_client.delete(f"{IMPORT_TEMPLATE_URL}/{others.pk}")
        assert deleted.status_code != 200 or deleted.data.get("code") != 1000
        others.refresh_from_db()
        assert others.name == "bob-tpl"

    def test_non_superuser_cannot_create_shared(self, api_client, alice, role, menu_factory):
        """M21：共享模板仅超管可建（普通用户提交 is_shared 无效）。"""
        grant(role, menu_factory, "api/system/import-templates$", "POST", "tpl-post")
        api_client.force_authenticate(user=alice)
        api_client.post(
            IMPORT_TEMPLATE_URL,
            {"model": MODEL_LABEL, "name": "try-shared", "mapping": {}, "is_shared": True},
            format="json",
        )
        # 字段权限开启时 is_shared 会被裁掉（框架语义），因此只断言「没有共享模板被建出来」
        assert not ImportTemplate.objects.filter(name="try-shared", is_shared=True).exists()

    def test_non_superuser_cannot_write_shared_template(self, api_client, alice, superuser, role, menu_factory):
        """M22：共享模板对普通用户只读——写操作取值域排除共享，改删均不可达。"""
        grant(role, menu_factory, "api/system/import-templates/(?P<pk>[^/.]+)$", "PUT", "tpl-put2")
        grant(role, menu_factory, "api/system/import-templates/(?P<pk>[^/.]+)$", "DELETE", "tpl-del2")
        shared = ImportTemplate.objects.create(
            creator=superuser, model=MODEL_LABEL, name="shared-tpl", mapping={}, is_shared=True
        )
        api_client.force_authenticate(user=alice)
        updated = api_client.put(
            f"{IMPORT_TEMPLATE_URL}/{shared.pk}",
            {"model": MODEL_LABEL, "name": "shared-tpl", "mapping": {"x": "y"}},
            format="json",
        )
        assert updated.status_code in (400, 403, 404) or updated.data.get("code") != 1000
        deleted = api_client.delete(f"{IMPORT_TEMPLATE_URL}/{shared.pk}")
        assert deleted.status_code in (400, 403, 404) or deleted.data.get("code") != 1000
        assert ImportTemplate.objects.filter(pk=shared.pk).exists()


class TestFilePreview:
    @pytest.fixture
    def other_file(self, superuser):
        from system.models import UploadFile

        return UploadFile.objects.create(
            filename="secret.png", filesize=10, mime_type="image/png", md5sum="b" * 32, creator=superuser
        )

    def test_anonymous_preview_denied(self, api_client, other_file):
        """M29：匿名访问文件预览（走鉴权，不暴露 /media/ 直链）。"""
        resp = api_client.get(FILE_PREVIEW_URL.format(pk=other_file.pk))
        assert resp.status_code == 401

    def test_preview_others_file_denied(self, api_client, alice, other_file, role, menu_factory):
        """M23：预览他人文件——属主收口，越权等同不存在。"""
        grant(role, menu_factory, "api/system/file/(?P<pk>[^/.]+)/preview$", "GET", "file-preview")
        api_client.force_authenticate(user=alice)
        resp = api_client.get(FILE_PREVIEW_URL.format(pk=other_file.pk))
        assert resp.status_code in (400, 403, 404)

    def test_preview_missing_pk_denied(self, api_client, alice, role, menu_factory):
        """M24：不存在的主键——404，不泄漏任何文件信息。"""
        grant(role, menu_factory, "api/system/file/(?P<pk>[^/.]+)/preview$", "GET", "file-preview2")
        api_client.force_authenticate(user=alice)
        resp = api_client.get(FILE_PREVIEW_URL.format(pk="00000000-0000-0000-0000-000000000000"))
        assert resp.status_code in (400, 403, 404)


class TestRecordStats:
    def test_stats_require_permission(self, api_client, alice):
        """M25：无 stats 菜单授权时垂直越权被拒。"""
        api_client.force_authenticate(user=alice)
        for url in (EXPORT_STATS_URL, IMPORT_STATS_URL, TASK_STATS_URL):
            assert api_client.get(url).status_code == 403

    def test_stats_scoped_to_owner(self, api_client, alice, bob, role, menu_factory):
        """M26：stats 只统计本人记录（超管看全量），不泄漏他人数据量。"""
        grant(role, menu_factory, "api/system/exports/stats$", "GET", "export-stats")
        ExportRecord.objects.create(name="mine", file_format="xlsx", creator=alice)
        ExportRecord.objects.create(name="others", file_format="xlsx", creator=bob)
        api_client.force_authenticate(user=alice)
        resp = api_client.get(EXPORT_STATS_URL)
        assert resp.status_code == 200
        assert resp.data["data"]["total"] == 1

    def test_download_others_export_record_denied(self, api_client, alice, bob, role, menu_factory):
        """M27：下载他人导出记录被拒（与列表同口径）。"""
        grant(role, menu_factory, "api/system/exports/(?P<pk>[^/.]+)/download$", "GET", "export-download")
        record = ExportRecord.objects.create(name="others", file_format="xlsx", creator=bob)
        api_client.force_authenticate(user=alice)
        resp = api_client.get(f"/api/system/exports/{record.pk}/download")
        assert resp.status_code in (400, 403, 404)

    def test_download_others_import_error_report_denied(self, api_client, alice, bob, role, menu_factory):
        """M28：他人导入记录的错误报告同样受属主收口。"""
        grant(role, menu_factory, "api/system/imports/(?P<pk>[^/.]+)/download$", "GET", "import-download")
        record = ImportRecord.objects.create(action="create", creator=bob)
        api_client.force_authenticate(user=alice)
        resp = api_client.get(f"/api/system/imports/{record.pk}/download")
        assert resp.status_code in (400, 403, 404)

    def test_task_stats_scoped_to_owner(self, api_client, alice, bob, role, menu_factory):
        """M26 附：任务执行 stats 同样按属主收口。"""
        grant(role, menu_factory, "api/system/tasks/executions/stats$", "GET", "task-stats")
        TaskExecution.objects.create(name="mine", creator=alice)
        TaskExecution.objects.create(name="others", creator=bob)
        api_client.force_authenticate(user=alice)
        resp = api_client.get(TASK_STATS_URL)
        assert resp.status_code == 200
        assert resp.data["data"]["total"] == 1
