# -*- coding: utf-8 -*-
"""权限可视化端点集成测试：GET /api/system/user|role/{pk}/preview 与 POST preview/trial。

覆盖：认证边界（401）/ 接口权限（403 垂直越权、方法与路径精确匹配）/ 三层数据正确性 /
规则解码文案 / 数据权限分组（个人 + 部门祖先链）/ 缓存旁路（授权后立即可见）/
试算（白名单、菜单上下文校验、count+sql、超管旁路）/ 角色预览（含持有用户水平越权过滤）。

约定（与越权矩阵一致）：权限结果按用户+方法缓存 24h，授权布置在首个请求前完成；
preview 取数直查 DB，故"先请求后补授权"用例用于验证缓存旁路。
"""
import json

import pytest

from demo.models import Book
from system.models import DataPermission, FieldPermission, ModeTypeAbstract, ModelLabelField

pytestmark = pytest.mark.django_db

PREVIEW_PATH = "api/system/user/(?P<pk>[^/.]+)/preview$"
TRIAL_PATH = "api/system/user/(?P<pk>[^/.]+)/preview/trial$"
ROLE_PREVIEW_PATH = "api/system/role/(?P<pk>[^/.]+)/preview$"


def preview_url(user) -> str:
    return f"/api/system/user/{user.pk}/preview"


def trial_url(user) -> str:
    return f"/api/system/user/{user.pk}/preview/trial"


def role_preview_url(role) -> str:
    return f"/api/system/role/{role.pk}/preview"


def grant_preview_menus(role, menu_factory, with_trial=True):
    """授予用户页 preview（GET）与 previewTrial（POST）权限码。"""
    preview_menu = menu_factory(name="preview:SystemUser", path=PREVIEW_PATH, method="GET")
    role.menu.add(preview_menu)
    if with_trial:
        trial_menu = menu_factory(name="previewTrial:SystemUser", path=TRIAL_PATH, method="POST")
        role.menu.add(trial_menu)
    return preview_menu


def make_user_self_scope(name="仅本人用户"):
    """system.user 模型「仅本人」数据权限（field=pk 精确匹配本人行）。"""
    return DataPermission.objects.create(
        name=name,
        rules=[{"table": "system.userinfo", "field": "pk", "type": "value.user.id", "value": "*", "match": "exact"}],
    )


def make_book_registry():
    """demo.book 数据权限注册表根节点（试算白名单）。"""
    return ModelLabelField.objects.create(
        name="demo.book", label="书籍", field_type=ModelLabelField.FieldChoices.DATA
    )


def make_owner_book_permission(name="仅本人书籍"):
    return DataPermission.objects.create(
        name=name,
        rules=[{"table": "demo.book", "field": "admin", "type": "value.user.id", "value": "*", "match": "exact"}],
    )


@pytest.fixture
def books(superuser, normal_user):
    """(他人的书, 自己的书)：以 admin 字段判定归属（Book.file 为必填 FK）。"""
    from system.models import UploadFile

    upload = UploadFile.objects.create(
        filename="cover.png", filesize=100, mime_type="image/png", md5sum="a" * 32, creator=superuser
    )
    other = Book.objects.create(
        name="别人的书", isbn="other-1", author="a", admin=superuser, admin2=superuser, file=upload
    )
    own = Book.objects.create(
        name="自己的书", isbn="own-1", author="a", admin=normal_user, admin2=normal_user, file=upload
    )
    return other, own


# ---------- 认证边界 / 接口权限 ----------


def test_preview_anonymous_401(api_client, normal_user):
    response = api_client.get(preview_url(normal_user))
    assert response.status_code == 401


def test_preview_without_code_403(api_client, normal_user):
    """无 preview 权限码的管理端请求 → 403（垂直越权，M03 抄法）。"""
    api_client.force_authenticate(user=normal_user)
    response = api_client.get(preview_url(normal_user))
    assert response.status_code == 403


def test_trial_requires_own_code(api_client, normal_user, role, menu_factory):
    """仅授 preview（GET）码时打 trial（POST）→ 403（路径 $ 锚定，方法精确匹配）。"""
    grant_preview_menus(role, menu_factory, with_trial=False)
    api_client.force_authenticate(user=normal_user)
    response = api_client.post(trial_url(normal_user), {"model": "system.user"}, format="json")
    assert response.status_code == 403


# ---------- 用户预览：契约与三层数据 ----------


def test_superuser_preview_contract(auth_client, normal_user):
    response = auth_client.get(preview_url(normal_user))
    assert response.status_code == 200
    data = response.data["data"]
    assert data["user"]["username"] == "zhangsan"
    assert data["user"]["roles"][0]["code"] == "common"
    # normal_user 的角色未授权任何菜单 → 空树/空码，契约字段齐全
    assert data["menu_tree"] == []
    assert data["api_permissions"] == []
    for key in ("enabled", "has_any_grant", "personal", "dept_chain", "semantic_note"):
        assert key in data["data_permissions"]
    assert data["data_permissions"]["has_any_grant"] is False
    assert data["field_permissions"] == []
    assert set(data["summary"]) == {"menu_count", "api_code_count", "data_permission_count", "field_permission_count"}


def test_preview_self_with_code(api_client, normal_user, role, menu_factory):
    """授 preview 码 + system.user 仅本人数据权限 → 可预览自己且个人授权出现在明细中。"""
    grant_preview_menus(role, menu_factory, with_trial=False)
    normal_user.rules.add(make_user_self_scope())
    api_client.force_authenticate(user=normal_user)
    response = api_client.get(preview_url(normal_user))
    assert response.status_code == 200
    data = response.data["data"]
    assert data["user"]["pk"] == str(normal_user.pk)
    personal = data["data_permissions"]["personal"]
    assert len(personal) == 1
    assert personal[0]["rules"][0]["type_text"] == "目标用户本人"
    assert normal_user.username in personal[0]["rules"][0]["value_text"]
    assert data["api_permissions"][0]["code"] == "preview:SystemUser"
    assert data["api_permissions"][0]["method"] == "GET"


def test_preview_superuser_bypass_flag(auth_client, superuser):
    response = auth_client.get(preview_url(superuser))
    assert response.status_code == 200
    data = response.data["data"]
    assert data["user"]["is_superuser"] is True
    assert data["data_permissions"]["superuser_bypass"] is True


# ---------- 规则解码 ----------


def test_decode_role_ids_and_exclude(normal_user, role):
    dp = DataPermission.objects.create(
        name="指定角色",
        rules=[
            {"table": "demo.book", "field": "name", "type": "value.table.role.ids",
             "value": json.dumps([str(role.pk)]), "match": "in"},
            {"table": "demo.book", "field": "isbn", "type": "value.text", "value": "secret", "match": "exact",
             "exclude": True},
        ],
    )
    from system.utils.permission_preview import decode_data_permission

    decoded = decode_data_permission(dp, normal_user)
    assert decoded["rules"][0]["value_text"] == role.name
    assert decoded["rules"][0]["type_text"] == "指定角色"
    assert decoded["rules"][1]["exclude"] is True
    # 仅两条规则保留原 mode_type；单条规则强制或模式
    assert decoded["mode_type"] == dp.mode_type


def test_decode_single_rule_forced_or(normal_user):
    """单条规则的授权强制或模式（get_filter_q_base L35-36 语义）。"""
    dp = DataPermission.objects.create(
        name="且模式单规则",
        mode_type=ModeTypeAbstract.ModeChoices.AND,
        rules=[{"table": "demo.book", "field": "name", "type": "value.text", "value": "x", "match": "exact"}],
    )
    from system.utils.permission_preview import decode_data_permission

    decoded = decode_data_permission(dp, normal_user)
    assert decoded["rule_text"].startswith("或模式")


def test_decode_all_ignored_in_and_mode(normal_user):
    """且模式下 value.all 被忽略（get_filter_q_base L40-41 语义）；需两条规则才保持且模式。"""
    dp = DataPermission.objects.create(
        name="且模式含all",
        mode_type=ModeTypeAbstract.ModeChoices.AND,
        rules=[
            {"table": "demo.book", "field": "admin", "type": "value.all", "value": "*", "match": "all"},
            {"table": "demo.book", "field": "isbn", "type": "value.text", "value": "x-1", "match": "exact"},
        ],
    )
    from system.utils.permission_preview import decode_data_permission

    decoded = decode_data_permission(dp, normal_user)
    assert decoded["rules"][0]["value_text"] == "且模式下被忽略"


# ---------- 数据权限分组与缓存旁路 ----------

def test_data_permission_dept_chain_grouping(normal_user, role, dept):
    """部门链分组：self 与 ancestor 分层展示；个人授权单独一组。"""
    parent = type(dept).objects.create(name="总公司", code="hq")
    dept.parent = parent
    dept.save()
    parent.rules.add(make_owner_book_permission("上级规则"))
    normal_user.dept = dept
    normal_user.save()
    normal_user.rules.add(make_owner_book_permission("个人规则"))

    from system.utils.permission_preview import get_user_data_permissions

    result = get_user_data_permissions(normal_user)
    assert len(result["personal"]) == 1
    assert len(result["dept_chain"]) == 2
    assert result["dept_chain"][0]["relation"] == "self"
    assert result["dept_chain"][0]["dept"]["name"] == "研发部"
    assert result["dept_chain"][1]["relation"] == "ancestor"
    assert result["dept_chain"][1]["dept"]["name"] == "总公司"
    assert result["has_any_grant"] is True


def test_preview_cache_bypass(auth_client, normal_user, role, menu_factory):
    """缓存旁路核心回归：追加授权后不失效任何缓存，预览立即可见新码。"""
    grant_preview_menus(role, menu_factory, with_trial=False)
    response = auth_client.get(preview_url(normal_user))
    codes = {item["code"] for item in response.data["data"]["api_permissions"]}
    assert codes == {"preview:SystemUser"}

    # 追加一条新码（不做任何缓存失效），再次预览应立即出现
    list_menu = menu_factory(name="list:SystemUser", path="api/system/user$", method="GET")
    role.menu.add(list_menu)
    response = auth_client.get(preview_url(normal_user))
    codes = {item["code"] for item in response.data["data"]["api_permissions"]}
    assert codes == {"preview:SystemUser", "list:SystemUser"}


# ---------- 试算 ----------


def test_trial_rejects_unregistered_model(api_client, normal_user, role, menu_factory):
    grant_preview_menus(role, menu_factory)
    api_client.force_authenticate(user=normal_user)
    response = api_client.post(trial_url(normal_user), {"model": "demo.book"}, format="json")
    assert response.status_code == 400


def test_trial_rejects_invisible_menu(api_client, normal_user, role, menu_factory):
    grant_preview_menus(role, menu_factory)
    make_book_registry()
    other_menu = menu_factory(name="SystemBook", path="/demo/book/index", method=None, menu_type=1)
    api_client.force_authenticate(user=normal_user)
    response = api_client.post(trial_url(normal_user), {"model": "demo.book", "menu": str(other_menu.pk)},
                               format="json")
    assert response.status_code == 400


def test_trial_count_and_sql(api_client, normal_user, role, menu_factory, books):
    """仅本人规则 + 两本书 → 命中 1 行，SQL 含 WHERE 片段。"""
    grant_preview_menus(role, menu_factory)
    make_book_registry()
    normal_user.rules.add(make_user_self_scope())
    normal_user.rules.add(make_owner_book_permission())
    api_client.force_authenticate(user=normal_user)
    response = api_client.post(trial_url(normal_user), {"model": "demo.book"}, format="json")
    assert response.status_code == 200
    data = response.data["data"]
    assert data["count"] == 1
    assert "WHERE" in data["sql"].upper()
    assert data["note"] is None


def test_trial_superuser_note(auth_client, superuser, books):
    make_book_registry()
    response = auth_client.post(trial_url(superuser), {"model": "demo.book"}, format="json")
    assert response.status_code == 200
    data = response.data["data"]
    assert data["count"] == 2
    assert data["note"] and "超级管理员" in data["note"]


def test_trial_generic_context_without_menu(api_client, normal_user, role, menu_factory, books):
    """menu=null 走通用授权上下文（dq 的 menu__isnull=True 分支）。"""
    grant_preview_menus(role, menu_factory)
    make_book_registry()
    normal_user.rules.add(make_user_self_scope())
    normal_user.rules.add(make_owner_book_permission())
    api_client.force_authenticate(user=normal_user)
    response = api_client.post(trial_url(normal_user), {"model": "demo.book", "menu": None}, format="json")
    assert response.status_code == 200
    assert response.data["data"]["count"] == 1


# ---------- 角色预览 ----------


def test_role_preview_contract(auth_client, role, normal_user, menu_factory):
    page_menu = menu_factory(name="SystemRole", path="/system/role/index", method=None, menu_type=1)
    role.menu.add(page_menu)
    model_field = ModelLabelField.objects.create(
        name="demo.book", label="书籍", field_type=ModelLabelField.FieldChoices.ROLE
    )
    child = ModelLabelField.objects.create(name="name", label="书名", parent=model_field,
                                           field_type=ModelLabelField.FieldChoices.ROLE)
    fp = FieldPermission.objects.create(role=role, menu=page_menu)
    fp.field.add(child)

    response = auth_client.get(role_preview_url(role))
    assert response.status_code == 200
    data = response.data["data"]
    assert data["role"]["code"] == "common"
    assert data["menu_tree"][0]["name"] == "SystemRole"
    assert data["field_permissions"][0]["menu"]["title"] == page_menu.meta.title
    assert data["field_permissions"][0]["models"][0]["fields"] == ["name"]
    assert data["users"]["total"] >= 1
    assert any(user["username"] == "zhangsan" for user in data["users"]["list"])
    assert data["users"]["truncated"] is False


def test_role_preview_filters_users_by_caller_scope(api_client, normal_user, role, menu_factory):
    """水平越权防线：调用者仅能看到角色本身（system.role 自我范围），持有用户列表为空。"""
    preview_role_menu = menu_factory(name="preview:SystemRole", path=ROLE_PREVIEW_PATH, method="GET")
    role.menu.add(preview_role_menu)
    # 授予 system.userrole 上 code=common 的可见范围（否则 get_object 404），但不授予 system.user 范围
    normal_user.rules.add(DataPermission.objects.create(
        name="仅本角色可见",
        rules=[{"table": "system.userrole", "field": "code", "type": "value.text", "value": "common",
                "match": "exact"}],
    ))
    api_client.force_authenticate(user=normal_user)
    response = api_client.get(role_preview_url(role))
    assert response.status_code == 200
    data = response.data["data"]
    assert data["role"]["pk"] == str(role.pk)
    assert data["users"]["total"] == 0
    assert data["users"]["list"] == []
