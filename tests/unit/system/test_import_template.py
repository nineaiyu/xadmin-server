# -*- coding: utf-8 -*-
"""导入列映射模板（ImportTemplate）：CRUD / 可见域 / 权限收口 + 三入口按模板或 mapping 导入。"""

import json

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from system.models.dict import DataDict
from system.models.import_ import ImportTemplate
from system.models.user import UserInfo
from system.views.admin.dict import DataDictViewSet
from system.views.admin.import_ import ImportTemplateViewSet

pytestmark = pytest.mark.django_db

MODEL_LABEL = "system.datadict"
BASE_URL = "/api/system/import-templates"

# 表头与字段名不一致的 CSV：必须经列映射才能导入
CSV_MAPPED = b"\xe7\xbc\x96\xe7\xa0\x81,\xe5\x90\x8d\xe7\xa7\xb0\nimp-map-color,Color\n"  # 编码,名称


def _api(method, viewset, action, url, user, data=None, pk=None, query="", raw_type=None):
    factory = APIRequestFactory()
    if method == "get":
        request = factory.get(f"{url}{query}")
    elif method == "post":
        if raw_type:
            request = factory.post(f"{url}{query}", data, content_type=raw_type)
        else:
            request = factory.post(f"{url}{query}", data or {}, format="json")
    elif method == "put":
        request = factory.put(f"{url}{query}", data or {}, format="json")
    else:
        request = factory.delete(f"{url}{query}")
    force_authenticate(request, user=user)
    view = viewset.as_view({method: action})
    return view(request, pk=pk) if pk is not None else view(request)


@pytest.fixture(autouse=True)
def _disable_field_permission(settings):
    """模板 CRUD 与字段权限正交：关闭字段裁剪，聚焦权限 / 唯一性 / 取值域。

    字段权限开启且未给模板菜单配白名单时，serializer 会把全部字段裁空
    （框架既有语义），本测试目标不是该语义。
    """
    settings.PERMISSION_FIELD_ENABLED = False


@pytest.fixture
def template_menus(role, menu_factory):
    """普通用户的模板权限码（与 loadjson/menu.json 同口径，单测不加载种子）。"""
    perms = [
        ("list:SystemImportTemplate", "api/system/import-templates$", "GET"),
        ("create:SystemImportTemplate", "api/system/import-templates$", "POST"),
        ("retrieve:SystemImportTemplate", "api/system/import-templates/(?P<pk>[^/.]+)$", "GET"),
        ("update:SystemImportTemplate", "api/system/import-templates/(?P<pk>[^/.]+)$", "PUT"),
        ("destroy:SystemImportTemplate", "api/system/import-templates/(?P<pk>[^/.]+)$", "DELETE"),
    ]
    menus = [menu_factory(name, path=path, method=method) for name, path, method in perms]
    role.menu.set(menus)
    return menus


def make_template(creator, name="tpl", mapping=None, is_shared=False):
    return ImportTemplate.objects.create(
        creator=creator,
        model=MODEL_LABEL,
        name=name,
        mapping=mapping or {"编码": "code", "名称": "label"},
        is_shared=is_shared,
    )


class TestTemplateCrud:
    def test_superuser_create_list_destroy(self, superuser):
        response = _api(
            "post",
            ImportTemplateViewSet,
            "create",
            BASE_URL,
            superuser,
            data={"model": MODEL_LABEL, "name": "我的模板", "mapping": {"编码": "code"}},
        )
        assert response.data["code"] == 1000
        template = ImportTemplate.objects.get(name="我的模板")
        assert template.creator == superuser
        assert template.mapping == {"编码": "code"}

        listed = _api("get", ImportTemplateViewSet, "list", BASE_URL, superuser, query=f"?model={MODEL_LABEL}")
        assert listed.data["data"]["total"] == 1

        deleted = _api(
            "delete", ImportTemplateViewSet, "destroy", f"{BASE_URL}/{template.pk}", superuser, pk=str(template.pk)
        )
        assert deleted.data["code"] == 1000
        assert not ImportTemplate.objects.filter(pk=template.pk).exists()

    def test_duplicate_name_rejected(self, superuser):
        make_template(superuser, name="dup")
        response = _api(
            "post",
            ImportTemplateViewSet,
            "create",
            BASE_URL,
            superuser,
            data={"model": MODEL_LABEL, "name": "dup", "mapping": {"编码": "code"}},
        )
        assert response.data["code"] != 1000

    def test_mapping_shape_cleaned(self, superuser):
        """mapping 清洗：键去空白、None → 空串（显式忽略该列）。"""
        response = _api(
            "post",
            ImportTemplateViewSet,
            "create",
            BASE_URL,
            superuser,
            data={"model": MODEL_LABEL, "name": "clean", "mapping": {" 编码 ": None, "名称": "label"}},
        )
        assert response.data["code"] == 1000
        template = ImportTemplate.objects.get(name="clean")
        assert template.mapping == {"编码": "", "名称": "label"}

    def test_invalid_mapping_rejected(self, superuser):
        response = _api(
            "post",
            ImportTemplateViewSet,
            "create",
            BASE_URL,
            superuser,
            data={"model": MODEL_LABEL, "name": "bad", "mapping": ["not-a-dict"]},
        )
        assert response.data["code"] != 1000


class TestTemplatePermission:
    def test_non_superuser_cannot_create_shared(self, normal_user, template_menus):
        response = _api(
            "post",
            ImportTemplateViewSet,
            "create",
            BASE_URL,
            normal_user,
            data={"model": MODEL_LABEL, "name": "共享模板", "mapping": {}, "is_shared": True},
        )
        assert response.data["code"] != 1000

    def test_non_superuser_can_create_personal(self, normal_user, template_menus):
        response = _api(
            "post",
            ImportTemplateViewSet,
            "create",
            BASE_URL,
            normal_user,
            data={"model": MODEL_LABEL, "name": "个人模板", "mapping": {"编码": "code"}},
        )
        assert response.data["code"] == 1000
        assert ImportTemplate.objects.get(name="个人模板").creator == normal_user

    def test_scope_personal_plus_shared(self, normal_user, template_menus, superuser):
        """普通用户可见 = 共享 + 本人；他人的个人模板既不可见也不可改删。"""
        other = UserInfo.objects.create_user(username="other-tpl", password="Test@123456")
        mine = make_template(normal_user, name="mine")
        shared = make_template(superuser, name="shared", is_shared=True)
        others = make_template(other, name="others")

        listed = _api("get", ImportTemplateViewSet, "list", BASE_URL, normal_user, query=f"?model={MODEL_LABEL}")
        names = {item["name"] for item in listed.data["data"]["results"]}
        assert names == {"mine", "shared"}
        assert mine.pk and shared.pk and others.pk  # 三个都存在，只是取值域不同

        # 他人个人模板：详情 404
        detail = _api(
            "get", ImportTemplateViewSet, "retrieve", f"{BASE_URL}/{others.pk}", normal_user, pk=str(others.pk)
        )
        assert detail.status_code in (400, 403, 404)

    @pytest.mark.django_db(transaction=True)
    def test_shared_template_readonly_for_non_superuser(self, normal_user, template_menus, superuser):
        """共享模板对普通用户只读：改 / 删均被取值域挡掉（框架 404→400 口径）且内容不变。

        ``transaction=True``：DRF 把 Http404 收敛为 400 但不会 set_rollback，
        测试外层 atomic 会进入不可查询状态（产品侧请求结束即回滚，无影响）。
        """
        shared = make_template(superuser, name="shared-ro", is_shared=True)
        response = _api(
            "put",
            ImportTemplateViewSet,
            "update",
            f"{BASE_URL}/{shared.pk}",
            normal_user,
            data={"model": MODEL_LABEL, "name": "renamed", "mapping": {}},
            pk=str(shared.pk),
        )
        assert response.status_code in (400, 403, 404)
        assert ImportTemplate.objects.filter(pk=shared.pk, name="shared-ro").exists()

        deleted = _api(
            "delete", ImportTemplateViewSet, "destroy", f"{BASE_URL}/{shared.pk}", normal_user, pk=str(shared.pk)
        )
        assert deleted.status_code in (400, 403, 404)
        assert ImportTemplate.objects.filter(pk=shared.pk).exists()


class TestImportMappingEntryPoints:
    """三入口（同步导入 / 校验 / 异步）共用的映射解析：template_id 优先，其次 mapping。"""

    def test_validate_with_template_mapping(self, superuser):
        template = make_template(superuser, name="validate-tpl")
        response = _api(
            "post",
            DataDictViewSet,
            "import_validate",
            "/api/system/dict/import-validate",
            superuser,
            data=CSV_MAPPED,
            query=f"?action=create&template_id={template.pk}",
            raw_type="text/csv",
        )
        data = response.data["data"]
        assert data["total"] == 1
        assert data["valid_count"] == 1
        # 字段名 → 原始表头：前端按源文件列名展示错误
        assert data["field_titles"] == {"code": "编码", "label": "名称"}

    def test_validate_with_inline_mapping(self, superuser):
        mapping = json.dumps({"编码": "code", "名称": "label"}, ensure_ascii=False)
        response = _api(
            "post",
            DataDictViewSet,
            "import_validate",
            "/api/system/dict/import-validate",
            superuser,
            data=CSV_MAPPED,
            query=f"?action=create&mapping={mapping}",
            raw_type="text/csv",
        )
        assert response.data["data"]["valid_count"] == 1

    def test_sync_import_with_template_mapping(self, superuser):
        template = make_template(superuser, name="sync-tpl")
        response = _api(
            "post",
            DataDictViewSet,
            "import_data",
            "/api/system/dict/import-data",
            superuser,
            data=CSV_MAPPED,
            query=f"?action=create&task=false&template_id={template.pk}",
            raw_type="text/csv",
        )
        assert response.data["code"] == 1000
        assert DataDict.objects.filter(code="imp-map-color", label="Color").exists()

    def test_async_import_with_template_mapping(self, superuser):
        template = make_template(superuser, name="async-tpl")
        response = _api(
            "post",
            DataDictViewSet,
            "import_async",
            "/api/system/dict/import-async",
            superuser,
            data=CSV_MAPPED,
            query=f"?action=create&template_id={template.pk}",
            raw_type="text/csv",
        )
        assert response.data["code"] == 1000
        # EAGER 环境同步执行：映射生效 → 落库成功
        assert DataDict.objects.filter(code="imp-map-color", label="Color").exists()

    def test_unknown_template_rejected(self, superuser):
        response = _api(
            "post",
            DataDictViewSet,
            "import_validate",
            "/api/system/dict/import-validate",
            superuser,
            data=CSV_MAPPED,
            query="?action=create&template_id=00000000-0000-0000-0000-000000000000",
            raw_type="text/csv",
        )
        assert response.status_code == 400

    def test_invalid_mapping_json_rejected(self, superuser):
        response = _api(
            "post",
            DataDictViewSet,
            "import_validate",
            "/api/system/dict/import-validate",
            superuser,
            data=CSV_MAPPED,
            query="?action=create&mapping=not-json",
            raw_type="text/csv",
        )
        assert response.status_code == 400

    def test_other_model_template_not_reusable(self, superuser):
        """模板按目标模型隔离：跨模型引用视为不存在。"""
        template = ImportTemplate.objects.create(
            creator=superuser, model="demo.book", name="other-model", mapping={"编码": "code"}
        )
        response = _api(
            "post",
            DataDictViewSet,
            "import_validate",
            "/api/system/dict/import-validate",
            superuser,
            data=CSV_MAPPED,
            query=f"?action=create&template_id={template.pk}",
            raw_type="text/csv",
        )
        assert response.status_code == 400

    def test_import_headers_action(self, superuser):
        """列映射步骤：回传首行表头 + 等名候选 + 目标字段选项（不落库）。"""
        response = _api(
            "post",
            DataDictViewSet,
            "import_headers",
            "/api/system/dict/import-headers",
            superuser,
            data=CSV_MAPPED,
            raw_type="text/csv",
        )
        data = response.data["data"]
        assert data["headers"] == ["编码", "名称"]
        assert data["candidates"] == ["", ""]  # 不做模糊推断，无候选
        field_values = {item["value"] for item in data["fields"]}
        assert {"code", "label"} <= field_values
        assert data["model"] == MODEL_LABEL

    def test_import_headers_candidates_hit_field_labels(self, superuser):
        body = "编码,label\nx,y\n".encode()
        response = _api(
            "post",
            DataDictViewSet,
            "import_headers",
            "/api/system/dict/import-headers",
            superuser,
            data=body,
            raw_type="text/csv",
        )
        assert response.data["data"]["candidates"] == ["", "label"]
