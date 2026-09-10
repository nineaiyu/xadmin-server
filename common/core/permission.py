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
from rest_framework.exceptions import PermissionDenied, NotAuthenticated
from rest_framework.permissions import BasePermission

from common.base.magic import MagicCacheData
from common.utils import get_logger
from server.utils import get_current_request, set_current_request
from system.services import FieldPermission, Menu

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
        return Menu.objects.filter(is_active=True).filter(q)
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
        for p_path, permission_item in permission_data.items():
            if re.match(f"/{p_path}", url):
                return permission_item
    return p_data


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

    校验口径 = 凭证 scope（空清单 = 不限，ADR-008 向后兼容）× 请求 path
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
        # 与 _resolve_menu_pk 同一套地址匹配口径（精确 path$ 优先，退化到前缀正则）
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

        if request.user.is_superuser:
            request.ignore_field_permission = True
            return True

        if self._match_white_url(request):
            request.ignore_field_permission = True
            return True

        permission_data = self._load_user_permission(request)
        menu_pk = self._resolve_menu_pk(request, permission_data)
        request.user.menu = menu_pk
        self._load_field_permission(request, menu_pk)
        return True

    @staticmethod
    def _match_white_url(request):
        """命中白名单 URL（按 HTTP 方法匹配）时放行。"""
        url = request.path_info
        for w_url, method in settings.PERMISSION_WHITE_URL.items():
            if re.match(w_url, url) and ("*" in method or request.method in method):
                return True
        return False

    @staticmethod
    def _load_user_permission(request):
        """加载用户权限菜单数据；依赖故障时 fail-closed（不放行、也不 500）。"""
        try:
            # 缓存基建修复后异常不再被吞掉（不再缓存空权限），此处 fail-closed
            # 兜底：依赖瞬时故障（如 DB 抖动）时不放行、也不 500，下一次请求自动重试
            return get_user_permission(request.user, request.method)
        except Exception as e:
            logger.error(f"get user permission failed. user:{request.user} method:{request.method} error:{e}")
            raise PermissionDenied(_("Permission denied"))

    @staticmethod
    def _resolve_menu_pk(request, permission_data):
        """解析当前请求命中的权限菜单主键（``permission_data[path] = (pk, model)``）。

        两条 URL 特例集中在此处，便于审计：
        1. ``search-columns`` 与对应 list 权限同口径；
        2. 导入导出接口未单独绑定模型时，回退到 list / create 菜单
           （异步导出/校验/异步导入/表头读取同此规则）。
        """
        url = request.path_info
        match_group = re.match("(?P<url>.*)/search-columns$", url)
        if match_group:
            url = match_group.group("url")
        p_data = menu_data = get_menu_pk(permission_data, url)
        if not p_data:
            raise PermissionDenied(_("Permission denied"))
        match_group = re.match("(?P<url>.*)/(export|import)-(data|async|validate|headers)$", url)
        if match_group and p_data[1] is None:
            url = match_group.group("url")
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
            raise PermissionDenied(_("Permission denied"))
