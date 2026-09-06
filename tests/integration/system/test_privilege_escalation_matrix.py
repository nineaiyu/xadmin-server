# -*- coding: utf-8 -*-
"""T5.3 越权矩阵用例集：水平/垂直越权 HTTP 集成测试（≥10 条入 CI）。

以 demo.Book 为数据权限载体、system.user 管理端接口为垂直越权目标，
按「认证边界 → 接口权限 → 自提权 → 数据权限 → 字段权限」五层矩阵验证
服务端防线，每条用例对应一个攻击面（矩阵编号 M01-M17）：

| 编号 | 层 | 攻击面 | 预期防线 |
|------|----|--------|----------|
| M01 | 认证边界 | 匿名访问受保护接口 | 401 |
| M02 | 认证边界 | 伪造 JWT 凭证 | 401 |
| M03 | 接口权限 | 管理端接口无菜单授权（垂直越权） | 403 |
| M04 | 接口权限 | GET 授权发起 POST 创建（方法越权） | 403 |
| M05 | 接口权限 | GET 授权调用批量删除动作 | 403 |
| M06 | 接口权限 | 列表授权不隐含详情授权（路由面精确） | 403 |
| M07 | 接口权限 | 白名单路由方法外访问（userinfo PATCH） | 403 |
| M08 | 接口权限 | 角色停用后授权立即吊销 | 403 |
| M09 | 接口权限 | 菜单停用后授权立即吊销 | 403 |
| M10 | 自提权 | 借用户管理接口给自己授予不可见角色 | 角色关系不变（字段白名单缺失=写忽略） |
| M11 | 数据权限 | 列表仅返回本人数据（水平隔离） | 他人数据不可见 |
| M12 | 数据权限 | 读他人单条数据 | 400（数据权限拦截） |
| M13 | 数据权限 | 改他人单条数据 | 400 且零副作用 |
| M14 | 数据权限 | 删他人单条数据 | 400 且零副作用 |
| M15 | 数据权限 | 未授权数据权限时默认拒绝（含本人数据） | 列表空 / 详情 400 |
| M16 | 数据权限 | 菜单作用域授权不跨菜单泄漏 | 他菜单下不可见 |
| M17 | 字段权限 | 字段白名单同时约束读与写 | 响应裁剪 / 写入忽略 |

约定：菜单授权遵循生产种子数据惯例（loadjson/menu.json）——列表路由以
`$` 精确锚定，详情路由用 `(?P<pk>[^/.]+)$` 正则；权限结果按用户+方法缓存
（MagicCacheData 24h），因此每个用例在发起首个请求前完成全部授权布置，
同一用例内不先请求再改权限。
"""
import pytest

from demo.models import Book
from system.models import DataPermission, FieldPermission, ModelLabelField, UserRole

pytestmark = pytest.mark.django_db

BOOK_LIST_URL = "/api/demo/book"
USER_LIST_URL = "/api/system/user"
USER_INFO_URL = "/api/system/userinfo"

LIST_PATH = "api/demo/book$"
DETAIL_PATH = "api/demo/book/(?P<pk>[^/.]+)$"
USER_DETAIL_PATH = "api/system/user/(?P<pk>[^/.]+)$"


def grant_menu(role, menu_factory, path, method, name=None):
    """给角色授予一条 PERMISSION 类型菜单（生产菜单 path 正则惯例）。"""
    menu = menu_factory(name or f"p-{method}-{path}", path=path, method=method)
    role.menu.add(menu)
    return menu


def make_owner_permission(name, table, field):
    """构造「仅本人」数据权限规则（type=value.user.id 注入当前用户主键）。"""
    return DataPermission.objects.create(
        name=name,
        rules=[{"table": table, "field": field, "type": "value.user.id", "value": "*", "match": "exact"}],
    )


def make_field_whitelist(role, menu, fields):
    """构建 role×menu 字段权限白名单（ModelLabelField 标签树：parent=模型 label）。"""
    model_field = ModelLabelField.objects.create(
        name="demo.book", label="书籍", field_type=ModelLabelField.FieldChoices.ROLE
    )
    children = [
        ModelLabelField.objects.create(
            name=f, label=f, parent=model_field, field_type=ModelLabelField.FieldChoices.ROLE
        )
        for f in fields
    ]
    fp = FieldPermission.objects.create(role=role, menu=menu)
    fp.field.add(*children)
    return fp


@pytest.fixture
def upload_file(superuser):
    from system.models import UploadFile

    return UploadFile.objects.create(
        filename="cover.png", filesize=100, mime_type="image/png", md5sum="a" * 32, creator=superuser
    )


@pytest.fixture
def books(superuser, normal_user, upload_file):
    """(他人的书, 自己的书)：以 admin 字段判定归属。"""
    other = Book.objects.create(
        name="别人的书", isbn="other-1", author="a", admin=superuser, admin2=superuser, file=upload_file
    )
    own = Book.objects.create(
        name="自己的书", isbn="own-1", author="a", admin=normal_user, admin2=normal_user, file=upload_file
    )
    return other, own


class TestAuthenticationBoundary:
    """M01/M02：认证边界。"""

    def test_anonymous_access_denied(self, api_client):
        resp = api_client.get(BOOK_LIST_URL)
        assert resp.status_code == 401
        assert resp.data["code"] == 401

    def test_forged_token_denied(self, api_client):
        api_client.credentials(HTTP_AUTHORIZATION="Bearer forged.token.value")
        resp = api_client.get(BOOK_LIST_URL)
        # 伪造凭证由 simplejwt 校验拒绝：HTTP 401，业务码 40001（token 失效）
        assert resp.status_code == 401
        assert resp.data["code"] == 40001


class TestVerticalApiPermission:
    """M03-M09：垂直越权——接口/菜单权限层。"""

    def test_admin_endpoint_denied_without_menu(self, api_client, normal_user):
        """M03：无任何菜单授权访问用户管理列表。"""
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(USER_LIST_URL)
        assert resp.status_code == 403
        assert resp.data["code"] == 403

    def test_get_grant_does_not_allow_create(self, api_client, normal_user, role, menu_factory):
        """M04：仅 GET 授权时发起 POST 创建（方法越权）。"""
        grant_menu(role, menu_factory, LIST_PATH, "GET")
        api_client.force_authenticate(user=normal_user)
        resp = api_client.post(BOOK_LIST_URL, {"name": "越权新增", "isbn": "hack"}, format="json")
        assert resp.status_code == 403
        assert not Book.objects.filter(name="越权新增").exists()

    def test_get_grant_does_not_allow_batch_destroy(self, api_client, normal_user, role, menu_factory):
        """M05：仅 GET 授权时调用批量删除动作。"""
        grant_menu(role, menu_factory, LIST_PATH, "GET")
        api_client.force_authenticate(user=normal_user)
        resp = api_client.post(f"{BOOK_LIST_URL}/batch-destroy", {"pks": ["x"]}, format="json")
        assert resp.status_code == 403

    def test_list_grant_does_not_imply_detail(self, api_client, normal_user, role, menu_factory, books):
        """M06：列表授权不隐含详情授权——即使数据权限允许，路由未授权仍拒绝。"""
        grant_menu(role, menu_factory, LIST_PATH, "GET")
        normal_user.rules.add(make_owner_permission("own-book", "demo.book", "admin"))
        own = books[1]
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(f"{BOOK_LIST_URL}/{own.pk}")
        assert resp.status_code == 403

    def test_white_url_method_scoped(self, api_client, normal_user):
        """M07：白名单路由按方法限定——userinfo GET 放行，PATCH 不放行。"""
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(USER_INFO_URL)
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        resp = api_client.patch(USER_INFO_URL, {"nickname": "越权改"}, format="json")
        assert resp.status_code == 403

    def test_inactive_role_revokes_access(self, api_client, normal_user, role, menu_factory):
        """M08：角色停用后授权立即吊销（缓存不放过期前的失效角色）。"""
        grant_menu(role, menu_factory, LIST_PATH, "GET")
        role.is_active = False
        role.save(update_fields=["is_active"])
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(BOOK_LIST_URL)
        assert resp.status_code == 403

    def test_inactive_menu_revokes_access(self, api_client, normal_user, role, menu_factory):
        """M09：菜单停用后授权立即吊销。"""
        menu = grant_menu(role, menu_factory, LIST_PATH, "GET")
        menu.is_active = False
        menu.save(update_fields=["is_active"])
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(BOOK_LIST_URL)
        assert resp.status_code == 403


class TestVerticalSelfEscalation:
    """M10：垂直越权——借管理接口自提权。"""

    def test_self_role_grant_blocked_by_related_field_filter(
        self, api_client, normal_user, role, menu_factory
    ):
        """M10：持有用户管理 PATCH 权限的用户，无法给自己授予数据权限不可见的角色。

        roles 关联字段写入受字段白名单约束：用户无 system.userrole 字段白名单 →
        roles 字段被整体裁剪（写忽略），目标角色不会被授予。防护观感为
        200-写忽略 或 400-拒绝，均属阻断，此处断言安全属性本身。
        （normal_user fixture 默认持有 role 以承载菜单授权，提权目标用另一角色。）
        """
        grant_menu(role, menu_factory, USER_DETAIL_PATH, "PATCH")
        # 数据权限仅允许看见本人资料（否则对象级就被拦截，测不到字段层防线）
        normal_user.rules.add(make_owner_permission("self-userinfo", "system.userinfo", "pk"))
        target_role = UserRole.objects.create(name="管理员", code="admin")
        api_client.force_authenticate(user=normal_user)
        api_client.patch(
            f"{USER_LIST_URL}/{normal_user.pk}", {"roles": [target_role.pk]}, format="json"
        )
        normal_user.refresh_from_db()
        assert target_role not in normal_user.roles.all()
        assert set(normal_user.roles.all()) == {role}


class TestHorizontalDataPermission:
    """M11-M16：水平越权——数据权限层。"""

    def _grant_own_only(self, normal_user, role, menu_factory):
        """列表 + 详情 GET 授权 + 仅本人数据权限（标准只读授权）。

        字段权限为白名单制（无白名单=响应字段全裁剪），断言行内容前需授予
        字段白名单，否则 results 行为空对象。
        """
        list_menu = grant_menu(role, menu_factory, LIST_PATH, "GET")
        make_field_whitelist(role, list_menu, ["pk", "name", "isbn"])
        grant_menu(role, menu_factory, DETAIL_PATH, "GET", name="p-book-detail")
        normal_user.rules.add(make_owner_permission("own-book", "demo.book", "admin"))

    def test_list_returns_only_own_records(self, api_client, normal_user, role, menu_factory, books):
        """M11：列表水平隔离——仅返回本人数据。"""
        self._grant_own_only(normal_user, role, menu_factory)
        other, own = books
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(BOOK_LIST_URL)
        assert resp.status_code == 200
        pks = [row["pk"] for row in resp.data["data"]["results"]]
        assert pks == [own.pk]
        assert other.pk not in pks

    def test_retrieve_other_record_denied(self, api_client, normal_user, role, menu_factory, books):
        """M12：读他人单条数据被数据权限拦截（观测不到 404/403 的差异细节）。"""
        self._grant_own_only(normal_user, role, menu_factory)
        other, _ = books
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(f"{BOOK_LIST_URL}/{other.pk}")
        # Http404 统一转 400，不向调用方暴露「数据不存在 vs 数据权限拦截」的差异细节
        assert resp.status_code == 400
        assert resp.data["code"] == 400

    def test_update_other_record_denied(self, api_client, normal_user, role, menu_factory, books):
        """M13：改他人单条数据——400 且零副作用。"""
        grant_menu(role, menu_factory, DETAIL_PATH, "PATCH", name="p-book-detail-patch")
        normal_user.rules.add(make_owner_permission("own-book", "demo.book", "admin"))
        other, _ = books
        api_client.force_authenticate(user=normal_user)
        resp = api_client.patch(
            f"{BOOK_LIST_URL}/{other.pk}", {"name": "被篡改"}, format="json"
        )
        assert resp.status_code == 400
        other.refresh_from_db()
        assert other.name == "别人的书"

    def test_delete_other_record_denied(self, api_client, normal_user, role, menu_factory, books):
        """M14：删他人单条数据——400 且数据仍在。"""
        grant_menu(role, menu_factory, DETAIL_PATH, "DELETE", name="p-book-detail-delete")
        normal_user.rules.add(make_owner_permission("own-book", "demo.book", "admin"))
        other, _ = books
        api_client.force_authenticate(user=normal_user)
        resp = api_client.delete(f"{BOOK_LIST_URL}/{other.pk}")
        assert resp.status_code == 400
        assert Book.objects.filter(pk=other.pk).exists()

    def test_default_deny_without_data_permission(
        self, api_client, normal_user, role, menu_factory, books
    ):
        """M15：有接口授权但未配置数据权限时默认拒绝——本人数据同样不可见。"""
        grant_menu(role, menu_factory, LIST_PATH, "GET")
        grant_menu(role, menu_factory, DETAIL_PATH, "GET", name="p-book-detail")
        _, own = books
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(BOOK_LIST_URL)
        assert resp.status_code == 200
        assert resp.data["data"]["total"] == 0
        resp = api_client.get(f"{BOOK_LIST_URL}/{own.pk}")
        assert resp.status_code == 400

    def test_menu_scoped_grant_does_not_leak_to_other_menu(
        self, api_client, normal_user, role, menu_factory, books
    ):
        """M16：菜单作用域授权不跨菜单泄漏——数据权限绑定其他菜单时本菜单不生效。"""
        grant_menu(role, menu_factory, LIST_PATH, "GET")
        foreign_menu = grant_menu(role, menu_factory, "api/other/stuff$", "GET", name="p-foreign")
        dp = make_owner_permission("scoped-own", "demo.book", "admin")
        dp.menu.add(foreign_menu)
        normal_user.rules.add(dp)
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(BOOK_LIST_URL)
        assert resp.status_code == 200
        assert resp.data["data"]["total"] == 0

    def test_menu_scoped_grant_applies_on_bound_menu(
        self, api_client, normal_user, role, menu_factory, books
    ):
        """M16 正向对照：数据权限绑定到当前菜单时正常生效（排除误伤回归）。"""
        menu = grant_menu(role, menu_factory, LIST_PATH, "GET")
        make_field_whitelist(role, menu, ["pk", "name", "isbn"])
        dp = make_owner_permission("scoped-own", "demo.book", "admin")
        dp.menu.add(menu)
        normal_user.rules.add(dp)
        _, own = books
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(BOOK_LIST_URL)
        assert resp.status_code == 200
        assert resp.data["data"]["total"] == 1
        assert resp.data["data"]["results"][0]["pk"] == own.pk


class TestFieldPermission:
    """M17：字段权限——白名单同时约束读与写。"""

    def test_read_response_trimmed_to_whitelist(
        self, api_client, normal_user, role, menu_factory, books
    ):
        """M17 读侧：列表响应字段被裁剪为白名单（pk/name），敏感字段不外泄。"""
        menu = grant_menu(role, menu_factory, LIST_PATH, "GET")
        normal_user.rules.add(make_owner_permission("own-book", "demo.book", "admin"))
        make_field_whitelist(role, menu, ["pk", "name"])
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(BOOK_LIST_URL)
        assert resp.status_code == 200
        results = resp.data["data"]["results"]
        assert results, "数据权限应放行本人数据"
        assert set(results[0].keys()) == {"pk", "name"}

    def test_write_of_unauthorized_field_ignored(
        self, api_client, normal_user, role, menu_factory, books
    ):
        """M17 写侧：白名单外字段（price）在写入路径被忽略，白名单内字段正常更新。"""
        menu = grant_menu(role, menu_factory, DETAIL_PATH, "PATCH", name="p-book-detail-patch")
        normal_user.rules.add(make_owner_permission("own-book", "demo.book", "admin"))
        make_field_whitelist(role, menu, ["pk", "name"])
        _, own = books
        api_client.force_authenticate(user=normal_user)
        resp = api_client.patch(
            f"{BOOK_LIST_URL}/{own.pk}", {"name": "改名成功", "price": 0.01}, format="json"
        )
        assert resp.status_code == 200
        assert resp.data["data"]["name"] == "改名成功"
        own.refresh_from_db()
        assert own.price == 999.99
