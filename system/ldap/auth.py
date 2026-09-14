#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LDAP bind 认证 backend。

挂载于 AUTHENTICATION_BACKENDS 首位，与 ModelBackend 并存：

- ``LDAP_AUTH_ENABLED=False``（默认）立即返回 None，行为与现状一致；
- ``LDAP_AUTH_PRIORITY="local_first"``（默认）时，本地已有可用密码的账号让位
  ModelBackend（目录密码不得遮蔽本地管理员密码）；仅「本地不存在/无可用密码」
  的用户尝试目录 bind；
- ``LDAP_AUTH_PRIORITY="ldap_first"`` 时先 bind 目录，本地密码仍作为链上后备；
- bind 失败 / 目录不可达一律 ``return None`` 落回 ModelBackend，**绝不抛异常
  阻断本地登录**（Django authenticate 链上未捕获异常会中断后续 backend）。

成功路径不签发 token、不记登录日志——那由 ``BasicLoginAPIView`` 既有链路
（``complete_login`` → MFA / 会话 / 日志收口）统一处理，本模块只在用户对象上
打 ``_ldap_authenticated`` 标记供 login_type 透传。
"""

from django.conf import settings
from django.contrib.auth.backends import BaseBackend
from django.db import IntegrityError

from common.utils import get_logger
from system.ldap.client import (
    LDAPException,
    entry_to_attrs,
    escape_filter,
    first_attr,
    get_attr_map,
    is_entry_disabled,
    normalize_dn,
    paged_search_entries,
    service_connection,
    user_connection,
)
from system.models import UserInfo
from system.models.ldap import LdapUserBinding

logger = get_logger(__name__)


class LdapBindBackend(BaseBackend):
    """服务账号定位用户 DN → 用户 DN bind 验密 → 解析/补建本地账号。"""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password or not settings.LDAP_AUTH_ENABLED:
            return None
        if self._defer_to_local(username):
            return None
        try:
            return self._bind_and_resolve(username, password)
        except LDAPException as e:
            logger.warning("LDAP bind failed for %s: %s", username, e)
            return None
        except Exception:  # noqa: BLE001 目录链路任何异常都不允许阻断本地登录
            logger.warning("LDAP authenticate unexpected error", exc_info=True)
            return None

    def get_user(self, user_id):
        try:
            user = UserInfo._default_manager.get(pk=user_id)
        except (UserInfo.DoesNotExist, ValueError, TypeError):
            return None
        return user if self.user_can_authenticate(user) else None

    def user_can_authenticate(self, user):
        """与 ModelBackend 同口径：禁用用户不参与认证。"""
        is_active = getattr(user, "is_active", None)
        return is_active or is_active is None

    # ---------------------------------------------------------------- 内部

    def _defer_to_local(self, username) -> bool:
        """local_first 优先级：本地存在可用密码的活账号时让位 ModelBackend。"""
        if settings.LDAP_AUTH_PRIORITY != "local_first":
            return False
        local = UserInfo.all_objects.filter(username__iexact=username).first()
        return bool(local and local.deleted_at is None and local.has_usable_password())

    def _bind_and_resolve(self, username, password):
        user_dn, attrs = self._locate_and_bind(username, password)
        user = self._resolve_or_create(user_dn, attrs)
        if user is not None:
            user._ldap_authenticated = True
        return user

    def _locate_and_bind(self, username, password):
        """服务账号搜索目标条目，再以用户 DN bind 验密。返回 (dn, attrs)。"""
        attr_map = get_attr_map()
        username_attr = attr_map.get("username", "sAMAccountName")
        search_filter = f"(&{settings.LDAP_USER_FILTER}({username_attr}={escape_filter(username)}))"
        attributes = sorted(set(attr_map.values()) | {"userAccountControl"})
        with service_connection() as conn:
            entries = paged_search_entries(conn, settings.LDAP_USER_SEARCH_BASE, search_filter, attributes)
        for entry in entries:
            attrs = entry_to_attrs(entry)
            value = first_attr(attrs, username_attr)
            if value and str(value).lower() == str(username).lower():
                if is_entry_disabled(attrs):
                    # 目录侧已禁用的账号直接拒绝（同步策略会置 is_active=False）
                    logger.info("LDAP account disabled in directory: %s", username)
                    raise LDAPException("account disabled in directory")
                # 用户 DN bind：密码错误抛 LDAPInvalidCredentialsResult（LDAPException）
                with user_connection(entry["dn"], password):
                    pass
                return entry["dn"], attrs
        # 目录中找不到该用户：交回本地 backend 链
        raise LDAPException("user not found in directory")

    def _resolve_or_create(self, user_dn, attrs):
        user_dn = normalize_dn(user_dn)
        binding = LdapUserBinding.objects.filter(dn=user_dn).first()
        if binding:
            user = binding.user
            if user is None or user.deleted_at is not None:
                # 绑定指向已删除账号（回收站用户名仍被占用），fail-closed
                return None
            return user

        username = first_attr(attrs, get_attr_map().get("username", "sAMAccountName"))
        username = str(username).strip() if username else ""
        if not username:
            return None
        local = UserInfo.all_objects.filter(username__iexact=username).first()
        if local is not None:
            if local.deleted_at is not None:
                return None
            # 本地同名账号复用：自动补建绑定（目录接管后续同步，本地密码仍可登录）
            LdapUserBinding.objects.get_or_create(user=local, defaults={"dn": user_dn})
            return local
        if not settings.LDAP_AUTH_AUTO_CREATE:
            return None
        return self._create_binding_user(username, user_dn, attrs)

    def _create_binding_user(self, username, user_dn, attrs):
        """目录建号：无本地密码（杜绝本地爆破面），邮箱/手机冲突时置空不阻断。"""
        attr_map = get_attr_map()
        nickname = first_attr(attrs, attr_map.get("nickname", "cn")) or username
        email = first_attr(attrs, attr_map.get("email", "mail")) or ""
        phone = first_attr(attrs, attr_map.get("phone", "telephoneNumber")) or ""
        email = str(email) if not UserInfo.all_objects.filter(email=str(email)).exists() else ""
        phone = str(phone) if not UserInfo.all_objects.filter(phone=str(phone)).exists() else ""
        try:
            user = UserInfo.objects.create_user(username=username, nickname=str(nickname), email=email, phone=phone)
        except IntegrityError:
            # 并发首登竞态：重查复用，避免本次登录 500
            user = UserInfo.all_objects.filter(username__iexact=username).first()
            if user is None or user.deleted_at is not None:
                return None
        LdapUserBinding.objects.get_or_create(user=user, defaults={"dn": user_dn})
        return user
