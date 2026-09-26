#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : permission
# author : ly_13
# date : 6/6/2023
import re
import uuid

from django.conf import settings
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.permissions import BasePermission

from common.base.magic import MagicCacheData
from common.core import permission_meta
from common.core.modules import filter_menu_queryset
from common.utils import get_logger
from server.utils import get_current_request, set_current_request
from system.services import (
    FieldPermission,
    Menu,
    application_of_request,
    enforce_application_grant,
    resolve_request_menu_pk,
)

logger = get_logger(__name__)


def get_user_menu_queryset(user_obj):
    q = Q()
    has_role = False
    # 一次取出角色列表复用：原 exists()/count() + all() 会对同一关系重复查询
    roles = list(user_obj.roles.all())
    if roles:
        q |= Q(userrole__in=roles) & Q(userrole__is_active=True)
        has_role = True
    if user_obj.dept:
        q |= Q(userrole__deptinfo=user_obj.dept) & Q(userrole__deptinfo__is_active=True)
        has_role = True
    if has_role:
        # return get_filter_queryset(Menu.objects.filter(is_active=True).filter(q), user_obj)
        # 菜单通过角色控制，就不用再次通过数据权限过滤了，要不然还得两个地方都得配置
        # 功能模块裁剪：停用模块的菜单子树与权限码在此统一隐藏（未配置停用模块时零开销）
        return filter_menu_queryset(Menu.objects.filter(is_active=True).filter(q))
    return None


@MagicCacheData.make_cache(timeout=10, key_func=lambda *args: f"{args[0].pk}_{args[1]}")
def get_user_field_queryset(user_obj, menu):
    q = Q()
    data = {}
    has_q = False
    # 一次取出角色列表复用，避免 count() 与 all() 各查一次库
    roles = list(user_obj.roles.all())
    if roles:
        q |= Q(role__in=roles) & Q(role__is_active=True)
        has_q = True
    if user_obj.dept:
        q |= Q(role__deptinfo=user_obj.dept) & Q(role__deptinfo__is_active=True)
        has_q = True
    if has_q:
        # queryset = get_filter_queryset(FieldPermission.objects.filter(q), user_obj).filter(menu=menu)
        queryset = FieldPermission.objects.filter(q).filter(menu=menu)  # 用户查询用户权限，无需使用权限过滤
        for val in queryset.values_list("field__parent__name", "field__name").distinct():
            info = data.get(val[0], set())
            if info:
                info.add(val[1])
            else:
                data[val[0]] = {val[1]}
    return data


@MagicCacheData.make_cache(timeout=3600 * 24, key_func=lambda x, y: f"{x.pk}_{y}")
def get_user_permission(user_obj, method):
    menus = []
    menu_queryset = get_user_menu_queryset(user_obj)
    if menu_queryset:
        filter_kwargs = {"menu_type": Menu.MenuChoices.PERMISSION, "method": method}
        menus = menu_queryset.filter(**filter_kwargs).values_list("path", "pk", "model").distinct()
    return dict([(menu[0], menu[1:]) for menu in menus])


def get_menu_pk(permission_data, url):
    # 1.直接get api/system/permission$   /api/system/config/system
    p_data = permission_data.get(f"{url[1:]}$")
    if not p_data:
        # 回退分支（权限点 path 为存储的正则串，与 permission_sync/scan.py 同口径）：
        # 历史写法 re.match 无尾锚——`api/user` 会粘连命中 /api/userfoo（越权面）。
        # 收敛为段边界前缀：无 `$` 后缀时要求模式匹配后到达段边界（结尾或 `/`），
        # 子路径覆盖能力不变（api/user 仍覆盖 /api/user/1），仅堵死跨字符粘连；
        # 带 `$` 后缀（精确）语义不变。坏正则跳过该权限点（对齐 scan.find_covering
        # 的 re.error 防御），避免单个坏权限点让该用户所有受控请求 500。
        for p_path, permission_item in permission_data.items():
            pattern = f"/{p_path}" if p_path.endswith("$") else f"/{p_path}(/.*)?"
            try:
                if re.fullmatch(pattern, url):
                    return permission_item
            except re.error:
                continue
    return p_data


def user_has_permission(user, path: str, method: str = "GET") -> bool:
    """按权限点 path 判定用户是否具备该权限（与运行时访问控制同源）。

    用于「无独立路由、但需按权限点授权的功能开关」场景（如审批实例的
    ``scope=ongoing`` 管理视角）：权限点 path 与 menu.path 同格式（形如
    ``api/system/approval-instances/ongoing$``），命中的是菜单-角色授权关系，
    与 ``get_user_permission`` 缓存同源（改授权后随缓存失效生效）。
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    # 超管豁免与 IsAuthenticated 的运行时口径一致（超管常无角色绑定，按角色查会漏判）
    if getattr(user, "is_superuser", False):
        return True
    normalized = path.lstrip("/")
    data = get_user_permission(user, (method or "GET").upper())
    if data.get(normalized):
        return True
    return bool(get_menu_pk(data, f"/{normalized}"))


def match_permission_white_url(method: str, path: str) -> bool:
    """「HTTP 方法 + 路径」是否命中访问白名单（``settings.PERMISSION_WHITE_URL``）。

    白名单端点不要求菜单权限点（登录/匿名即可访问，语义见 settings/custom.py 注释），
    是运行时访问控制（IsAuthenticated）与 AI 动作业务权限预检的公共口径——
    两边必须同源，否则「运行时能访问、AI 动作预检无权限」类缺口会出现
    （如 /api/system/dashboard/* 对普通用户的 dashboard.overview 动作）。
    """
    if not method or not path:
        return False
    for w_url, methods in settings.PERMISSION_WHITE_URL.items():
        if re.match(w_url, path) and ("*" in methods or method.upper() in methods):
            return True
    return False


def resolve_pat_scopes(request):
    """解析本次请求的 PAT scope 清单，非 PAT 请求返回 None。

    - PAT 认证胜出：PersonalAccessTokenAuthentication 已把 scopes 挂 request.pat_scopes；
    - JWT 胜出但同请求携带 Pat 头（双 header）：认证链在首个成功认证器处短路，
      pat_scopes 缺失——此处从原始 Authorization 头解析凭证补校验（只取未过期且
      启用的凭证），防绕过 scope；
    - 凭证无效时按无 scope 处理（该请求的有效凭证是 JWT，PAT 头本身认证不过）。
    """
    scopes = getattr(request, "pat_scopes", None)
    if scopes is not None:
        return scopes
    header = request.META.get("HTTP_AUTHORIZATION", "")
    parts = header.split()
    if len(parts) != 2 or parts[0].lower() != "pat":
        return None
    from django.apps import apps
    from django.utils import timezone

    from common.core.auth import hash_pat_token

    token_model = apps.get_model("system", "PersonalAccessToken")
    pat = (
        token_model.objects.filter(token_hash=hash_pat_token(parts[1]), is_active=True)
        .only("scopes", "expired_at")
        .first()
    )
    if pat is None or (pat.expired_at and pat.expired_at <= timezone.now()):
        scopes = []
    else:
        scopes = pat.scopes or []
    request.pat_scopes = scopes
    return scopes


def check_pat_scope(request) -> bool:
    """PAT scope 判定：True 放行；False 表示当前凭证不允许访问该请求。

    校验口径 = 凭证 scope（空清单 = 不限，向后兼容）× 请求 path
    （条目可带方法前缀，形如 ``GET /api/system/user``，此时同时限定 HTTP 方法）。
    """
    scopes = resolve_pat_scopes(request)
    if scopes is None:
        return True
    from common.core.auth import path_allowed_by_scopes

    return path_allowed_by_scopes(request.path, scopes, getattr(request, "method", None))


def user_can_update_menu(user, url) -> bool:
    """当前用户是否拥有该请求地址对应资源的更新权限（PUT / PATCH 任一命中）。

    供脱敏「原文通道」门禁使用：只有具备更新权限的用户才需要原文，否则编辑弹窗
    拿到的掩码值会被回写（见 BaseModelSerializer.to_internal_value 的守护）。

    **按请求地址匹配而非菜单主键**：同一 path 的 GET / PUT / PATCH 是三条独立菜单
    （主键互不相同），按主键比对时 GET 请求永远不可能命中更新权限菜单，原文通道
    会被无条件关死（前端不接线时不易察觉）。
    """
    if not user or not user.pk or not url:
        return False
    for method in ("PUT", "PATCH"):
        try:
            permission_data = get_user_permission(user, method)
        except Exception as e:  # noqa: BLE001 权限查询失败按无更新权限处理
            logger.warning(f"check update permission failed. user:{user} error:{e}")
            continue
        # 与 _resolve_menu_pk 同一套地址匹配口径（精确 path$ 优先，退化到段边界前缀）
        if permission_data and get_menu_pk(permission_data, url):
            return True
    return False


class PatScopePermission(BasePermission):
    """PAT scope 校验权限类（保留供显式 permission_classes 清单引用）。

    默认权限链已由 ``IsAuthenticated`` 统一校验（见其 has_permission 说明），本类
    保留是为了：(1) 既有 `permission_classes = [IsAuthenticated, PatScopePermission]`
    写法与测试继续有效；(2) `permission_classes` 被整体覆写为不含 IsAuthenticated
    的视图（如仅登录即可访问的个人配置）仍可显式挂载。
    """

    message = _("PAT scope does not allow this path")

    def has_permission(self, request, view):
        return check_pat_scope(request)


class IsAuthenticated(BasePermission):
    """
    Allows access only to authenticated users.
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            raise NotAuthenticated(_("Unauthorized authentication"))

        request.request_uuid = getattr(get_current_request(), "request_uuid", uuid.uuid4())
        set_current_request(request)

        # PAT scope 统一校验：认证成功即校验，**不区分 JWT/PAT、也不区分默认链
        # 与显式 permission_classes 清单**——DRF 中 action 级 permission_classes
        # 会整体替换默认链，校验放在这里才不会被漏（scope 限制的是凭证本身，
        # 因此超管用 PAT 调接口同样受限）。
        if not check_pat_scope(request):
            raise PermissionDenied(_("PAT scope does not allow this path"))

        # 应用四级授权：应用凭证存在授权规则时走白名单（模型×动作×字段×行），
        # 无规则 = 兼容模式直接跳过。三个出口统一收敛（超管 / 白名单 URL 不豁免）。
        if request.user.is_superuser:
            request.ignore_field_permission = True
            self._check_application_grant(request, view)
            return True

        if self._match_white_url(request):
            request.ignore_field_permission = True
            self._check_application_grant(request, view)
            return True

        permission_data = self._load_user_permission(request)
        menu_pk = self._resolve_menu_pk(request, permission_data)
        request.user.menu = menu_pk
        self._load_field_permission(request, menu_pk)
        self._check_application_grant(request, view)
        return True

    @staticmethod
    def _check_application_grant(request, view):
        """应用四级授权校验（模型 × 动作级；字段/行级在各自消费点收敛）。

        超管与白名单 URL 出口未解析菜单上下文（``request.user.menu`` 为空）——
        应用凭证在此按请求路径在启用权限菜单里命中一次（动作段解析所需）。
        """
        if getattr(request.user, "menu", None) is None and application_of_request(request) is not None:
            request.user.menu = resolve_request_menu_pk(request)
        enforce_application_grant(request, view)

    @staticmethod
    def _match_white_url(request):
        """命中白名单 URL（按 HTTP 方法匹配）时放行。"""
        return match_permission_white_url(request.method, request.path_info)

    @staticmethod
    def _load_user_permission(request):
        """加载用户权限菜单数据；依赖故障时 fail-closed（不放行、也不 500）。"""
        try:
            # 缓存基建修复后异常不再被吞掉（不再缓存空权限），此处 fail-closed
            # 兜底：依赖瞬时故障（如 DB 抖动）时不放行、也不 500，下一次请求自动重试
            return get_user_permission(request.user, request.method)
        except Exception as e:
            logger.error(f"get user permission failed. user:{request.user} method:{request.method} error:{e}")
            raise PermissionDenied(_("Permission denied")) from None

    @staticmethod
    def _resolve_menu_pk(request, permission_data):
        """解析当前请求命中的权限菜单主键（``permission_data[path] = (pk, model)``）。

        子 action 的权限口径为声明式元数据（common/core/permission_meta.py），
        核心类只消费注册表、不再硬编码后缀清单：
        1. ``shared_list``（search-columns / suggestions / available-forms /
           user-options 等）：剥掉 URL 尾部后缀，按父级 list 权限同口径解析；
        2. ``parent_fallback``（export|import 系）：优先按自身权限点解析，
           未单独绑定模型时回退到父级 list / create 菜单。
        二开新增同类子 action 时，在 ViewSet 声明处改用对应装饰器即可，无需改本类。
        """
        url = request.path_info
        shared = permission_meta.shared_list_pattern()
        if shared:
            url = shared.sub("", url, count=1)
        p_data = menu_data = get_menu_pk(permission_data, url)
        if not p_data:
            raise PermissionDenied(_("Permission denied"))
        fallback = permission_meta.parent_fallback_pattern()
        if fallback:
            match_group = fallback.search(url)
            if match_group and p_data[1] is None:
                url = url[: match_group.start()]
                menu_data = get_menu_pk(permission_data, url)
        if not menu_data:
            menu_data = p_data
        return menu_data[0]

    @staticmethod
    def _load_field_permission(request, menu_pk):
        """装载字段级权限到 ``request.fields``（未启用字段权限时跳过）。"""
        if not settings.PERMISSION_FIELD_ENABLED:
            return
        try:
            request.fields = get_user_field_queryset(request.user, menu_pk)
        except Exception as e:
            logger.error(f"get user field permission failed. user:{request.user} menu:{menu_pk} error:{e}")
            raise PermissionDenied(_("Permission denied")) from None
