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
    return ModelLabelField.objects.create(name="demo.book", label="书籍", field_type=ModelLabelField.FieldChoices.DATA)


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
            {
                "table": "demo.book",
                "field": "name",
                "type": "value.table.role.ids",
                "value": json.dumps([str(role.pk)]),
                "match": "in",
            },
            {
                "table": "demo.book",
                "field": "isbn",
                "type": "value.text",
                "value": "secret",
                "match": "exact",
                "exclude": True,
            },
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
    """单条规则的授权展示为或模式（单条规则且/或行为等价，展示口径保留）。"""
    dp = DataPermission.objects.create(
        name="且模式单规则",
        mode_type=ModeTypeAbstract.ModeChoices.AND,
        rules=[{"table": "demo.book", "field": "name", "type": "value.text", "value": "x", "match": "exact"}],
    )
    from system.utils.permission_preview import decode_data_permission

    decoded = decode_data_permission(dp, normal_user)
    assert decoded["rule_text"].startswith("或模式")


def test_decode_all_ignored_in_and_mode(normal_user):
    """且模式下 value.all 被忽略（data_scope 代数：ALLOW 是 AND 单位元）；需两条规则才保持且模式。"""
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
    response = api_client.post(
        trial_url(normal_user), {"model": "demo.book", "menu": str(other_menu.pk)}, format="json"
    )
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
    child = ModelLabelField.objects.create(
        name="name", label="书名", parent=model_field, field_type=ModelLabelField.FieldChoices.ROLE
    )
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
    normal_user.rules.add(
        DataPermission.objects.create(
            name="仅本角色可见",
            rules=[
                {"table": "system.userrole", "field": "code", "type": "value.text", "value": "common", "match": "exact"}
            ],
        )
    )
    api_client.force_authenticate(user=normal_user)
    response = api_client.get(role_preview_url(role))
    assert response.status_code == 200
    data = response.data["data"]
    assert data["role"]["pk"] == str(role.pk)
    assert data["users"]["total"] == 0
    assert data["users"]["list"] == []


# ---------- 预览与运行时的口径一致性（第三轮复核） ----------


def test_inactive_dept_grant_not_effective(normal_user, dept):
    """停用部门上的授权不参与实际过滤：标 effective=False，且不计入 has_any_grant。

    守护：运行时 filter.py 只把 active 部门加入 active_chain，预览曾无条件展示整条祖先链。
    """
    dept.is_active = False
    dept.save(update_fields=["is_active"])
    dept.rules.add(make_owner_book_permission("停用部门规则"))
    normal_user.dept = dept
    normal_user.save()

    from system.utils.permission_preview import get_user_data_permissions

    result = get_user_data_permissions(normal_user)
    assert result["has_any_grant"] is False
    self_row = result["dept_chain"][0]
    assert self_row["is_active"] is False
    assert self_row["effective"] is False
    # 授权仍列出便于定位配置（不隐藏、但不误导为生效）
    assert len(self_row["permissions"]) == 1


def test_inactive_ancestor_dept_grant_not_effective(normal_user, dept):
    """祖先层停用同上（祖先链只保留 active 层）。"""
    parent = type(dept).objects.create(name="停用总公司", code="hq-inactive", is_active=False)
    dept.parent = parent
    dept.save()
    parent.rules.add(make_owner_book_permission("停用上级规则"))
    normal_user.dept = dept
    normal_user.save()

    from system.utils.permission_preview import get_user_data_permissions

    result = get_user_data_permissions(normal_user)
    assert result["has_any_grant"] is False
    assert result["dept_chain"][1]["relation"] == "ancestor"
    assert result["dept_chain"][1]["effective"] is False


def test_menu_scoped_grant_flagged_not_general_effective(normal_user, menu_factory):
    """绑定菜单的授权仅在对应菜单上下文生效：标 menu_scoped，不计入通用 has_any_grant。"""
    from system.models import Menu

    from system.utils.permission_preview import get_user_data_permissions

    dp = make_owner_book_permission("绑定菜单规则")
    dp.menu.add(menu_factory(name="书籍列表", menu_type=Menu.MenuChoices.MENU))
    normal_user.rules.add(dp)
    result = get_user_data_permissions(normal_user)
    assert result["personal"][0]["menu_scoped"] is True
    assert result["has_any_grant"] is False

    # 再补一条不绑菜单的同类授权 → 通用上下文下有生效授权
    normal_user.rules.add(make_owner_book_permission("通用规则"))
    assert get_user_data_permissions(normal_user)["has_any_grant"] is True


def test_menu_count_counts_all_nodes(auth_client, normal_user, role, menu_factory):
    """summary.menu_count 统计全部页面菜单节点（含子节点），不再只算根节点。"""
    from system.models import Menu

    parent = menu_factory(name="系统管理", menu_type=Menu.MenuChoices.DIRECTORY)
    child = menu_factory(name="用户管理", menu_type=Menu.MenuChoices.MENU, parent=parent)
    role.menu.add(parent, child)

    response = auth_client.get(preview_url(normal_user))
    assert response.status_code == 200
    assert response.data["data"]["summary"]["menu_count"] == 2


def test_role_preview_keeps_ancestor_of_bound_child(api_client, superuser, role, menu_factory):
    """角色只绑子菜单时预览树补齐祖先，避免孤儿子节点被当成根。"""
    from system.models import Menu

    parent = menu_factory(name="系统管理", menu_type=Menu.MenuChoices.DIRECTORY)
    child = menu_factory(name="用户管理", menu_type=Menu.MenuChoices.MENU, parent=parent)
    role.menu.add(child)

    api_client.force_authenticate(user=superuser)
    response = api_client.get(role_preview_url(role))
    assert response.status_code == 200
    tree = response.data["data"]["menu_tree"]
    assert len(tree) == 1
    assert tree[0]["title"] == "系统管理"
    assert tree[0]["children"][0]["title"] == "用户管理"


DEPT_PREVIEW_PATH = "api/system/dept/(?P<pk>[^/.]+)/preview$"


def dept_preview_url(dept) -> str:
    return f"/api/system/dept/{dept.pk}/preview"


def test_dept_preview_requires_code(api_client, normal_user, dept):
    """无 preview:SystemDept 权限码 → 403（与用户/角色预览同口径）。"""
    api_client.force_authenticate(user=normal_user)
    assert api_client.get(dept_preview_url(dept)).status_code == 403


def test_dept_preview_contract(auth_client, dept, role, normal_user, menu_factory):
    from system.models import Menu

    dept.leader = normal_user
    dept.save(update_fields=["leader"])
    normal_user.dept = dept
    normal_user.save(update_fields=["dept"])
    dept.roles.add(role)
    dept.rules.add(make_owner_book_permission("部门规则"))
    type(dept).objects.create(name="子部门", code="dept-child-1", parent=dept)
    role.menu.add(menu_factory(name="SystemDept", menu_type=Menu.MenuChoices.MENU))

    response = auth_client.get(dept_preview_url(dept))
    assert response.status_code == 200
    data = response.data["data"]
    assert data["dept"]["name"] == "研发部"
    assert data["dept"]["leader"]["username"] == "zhangsan"
    assert data["dept"]["child_count"] == 1
    assert data["dept"]["active_child_count"] == 1
    assert [item["code"] for item in data["roles"]] == ["common"]
    assert data["menu_tree"][0]["name"] == "SystemDept"
    assert data["field_permission_enabled"] in (True, False)
    assert data["notes"]

    # 部门维度主语是「部门成员」，不依赖某个具体用户
    rule = data["data_permissions"]["rules"][0]["rules"][0]
    assert rule["type_text"] == "目标用户本人"
    assert rule["value_text"] == "部门成员本人（各自）"
    assert data["data_permissions"]["has_any_grant"] is True
    assert data["users"]["total"] >= 1


def test_dept_preview_filters_users_by_caller_scope(api_client, normal_user, dept, menu_factory):
    """水平越权防线：调用者数据范围内看不到部门成员时，成员列表为空。"""
    role = normal_user.roles.first()
    role.menu.add(menu_factory(name="preview:SystemDept", path=DEPT_PREVIEW_PATH, method="GET"))
    # 授予 system.deptinfo 的可见范围（否则 get_object 404），但不授予 system.userinfo 范围
    normal_user.rules.add(
        DataPermission.objects.create(
            name="仅本部门可见",
            rules=[
                {
                    "table": "system.deptinfo",
                    "field": "code",
                    "type": "value.text",
                    "value": "dev",
                    "match": "exact",
                }
            ],
        )
    )
    api_client.force_authenticate(user=normal_user)

    response = api_client.get(dept_preview_url(dept))
    assert response.status_code == 200, response.data
    # normal_user 对 system.userinfo 无任何授权 → 看不到部门成员
    assert response.data["data"]["users"]["total"] == 0


# ---------- 试算草稿（配置页即时验证影响面） ----------


def test_trial_draft_rules_apply_and_widen_scope(api_client, normal_user, role, menu_factory, books):
    grant_preview_menus(role, menu_factory)
    make_book_registry()
    normal_user.rules.add(make_user_self_scope())  # 让调用者能看到自己（否则 get_object 404）
    api_client.force_authenticate(user=normal_user)

    # 无任何授权：看不到数据
    response = api_client.post(trial_url(normal_user), {"model": "demo.book"}, format="json")
    assert response.status_code == 200
    assert response.data["data"]["count"] == 0
    assert response.data["data"]["draft_applied"] is False

    # 草稿「全部数据」不落库，但参与本次试算
    draft = {
        "rules": [{"table": "demo.book", "field": "isbn", "type": "value.all", "value": "*", "match": "all"}],
        "mode_type": ModeTypeAbstract.ModeChoices.OR,
    }
    response = api_client.post(trial_url(normal_user), {"model": "demo.book", "draft": draft}, format="json")
    assert response.status_code == 200, response.data
    data = response.data["data"]
    assert data["draft_applied"] is True
    assert data["count"] == 2
    # 草稿不入库：库存授权数量不变
    assert not DataPermission.objects.filter(name="__draft__").exists()


def test_trial_draft_invalid_rules_rejected(api_client, normal_user, role, menu_factory, books):
    """草稿与保存走同一套写入校验，不能成为绕过校验的后门。"""
    grant_preview_menus(role, menu_factory)
    make_book_registry()
    normal_user.rules.add(make_user_self_scope())
    api_client.force_authenticate(user=normal_user)
    draft = {"rules": [{"table": "demo.book", "field": "creat0r", "type": "value.text", "value": "x"}]}
    response = api_client.post(trial_url(normal_user), {"model": "demo.book", "draft": draft}, format="json")
    assert response.status_code == 400


def test_trial_draft_menu_scoped_only_in_matching_context(api_client, normal_user, role, menu_factory, books):
    """草稿绑定了菜单：仅在对应菜单上下文参与试算，通用上下文不生效。"""
    grant_preview_menus(role, menu_factory)
    make_book_registry()
    menu = menu_factory(name="demo.book", menu_type=2)
    normal_user.rules.add(make_user_self_scope())
    api_client.force_authenticate(user=normal_user)
    draft = {
        "rules": [{"table": "demo.book", "field": "isbn", "type": "value.all", "value": "*", "match": "all"}],
        "menu": str(menu.pk),
    }
    response = api_client.post(trial_url(normal_user), {"model": "demo.book", "draft": draft}, format="json")
    assert response.status_code == 200, response.data
    assert response.data["data"]["draft_applied"] is False


def test_decode_dirty_value_falls_back(normal_user):
    """历史脏值（非法 JSON / 非预期结构）解码不抛异常，回退原始文案（预览不 500）。"""
    dp = DataPermission.objects.create(
        name="脏值规则",
        rules=[
            {"table": "demo.book", "field": "name", "type": "value.date", "value": "not-json", "match": "exact"},
            {
                "table": "demo.book",
                "field": "name",
                "type": "value.datetime.range",
                "value": "oops",
                "match": "exact",
            },
            {"table": "demo.book", "field": "name", "type": "value.table.role.ids", "value": "not-json", "match": "in"},
        ],
    )
    from system.utils.permission_preview import decode_data_permission

    decoded = decode_data_permission(dp, normal_user)
    assert decoded["rules"][0]["value_text"] == "not-json"
    assert decoded["rules"][1]["value_text"] == "oops"
    assert decoded["rules"][2]["value_text"] == "（无）"
