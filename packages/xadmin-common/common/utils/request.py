#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : request
# author : ly_13
# date : 6/27/2023
import base64
import ipaddress
import json
import re

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.utils.module_loading import import_string
from rest_framework.throttling import BaseThrottle
from rest_framework_simplejwt.authentication import JWTAuthentication
from user_agents import parse

from common.core.auth import GetUserFromAccessToken
from common.core.utils import get_doc_first_line
from common.settings_contract import kernel_required_setting, kernel_setting

#: multipart 请求体解析上限（只解析小表单的字段名；超大请求不读正文，避免整包进内存）
MULTIPART_FIELD_PARSE_LIMIT = 64 * 1024


def get_request_user(request):
    """
    获取请求user
    (1)如果request里的user没有认证,那么则手动认证一次
    :param request:
    :return:
    """
    user: AbstractBaseUser = getattr(request, "user", None)
    if user and user.is_authenticated:
        return user
    try:
        auth_result = JWTAuthentication().authenticate(request)
        if auth_result is None:  # 未命中凭证时沿用「抛错进入 refresh 兜底」的既有语义
            raise ValueError("authentication failed")
        user, token = auth_result
    except Exception:
        # 访问令牌不可用（过期/非法/未携带）：进入 refresh 兜底链路
        try:
            body = getattr(request, "request_data", {})
            refresh_token = body.get("refresh")
            if refresh_token:
                token = GetUserFromAccessToken(refresh_token)
                auth_class = import_string(
                    kernel_required_setting("REST_FRAMEWORK").get("DEFAULT_AUTHENTICATION_CLASSES")[0]
                )()
                user = auth_class.get_user(token)
        except Exception:
            # refresh 令牌同样不可用：按匿名用户处理（由视图权限决定 401）
            pass
    return user or AnonymousUser()


def _normalize_ip(value):
    """归一化单个地址：剥离空白/引号，兼容 "ipv4:port"、"[ipv6]:port" 等非标准写法。"""
    if not value:
        return ""
    value = str(value).strip().strip('"')
    if not value:
        return ""
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 0 else value
    if value.count(":") == 1 and "." in value:
        # ipv4:port（X-Forwarded-For 中偶见），IPv6 字面量不含 "."，不会误伤
        return value.split(":")[0].strip()
    return value


def _is_trusted_proxy(ip):
    """直连地址/转发地址是否命中 TRUSTED_PROXY_IPS（单个 IP 或 CIDR）。"""

    trusted = kernel_setting("TRUSTED_PROXY_IPS") or []
    if not trusted or not ip:
        return False
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for cidr in trusted:
        try:
            if address in ipaddress.ip_network(str(cidr), strict=False):
                return True
        except ValueError:
            continue
    return False


def get_request_ip(request):
    """
    获取请求 IP（防伪造：X-Forwarded-For 仅在直连地址为可信代理时参与解析）。

    - 直连地址（REMOTE_ADDR）不在 TRUSTED_PROXY_IPS 内：请求头完全不可信，
      直接使用直连地址——攻击者伪造 XFF 无法影响 IP 登录封禁 / PAT IP 白名单判定；
    - 直连地址是可信代理：从 XFF 右往左取第一个非可信地址，逐跳剥离可信代理，
      得到最接近客户端的真实地址（内置 HTTP 反代 xadmin-api-conf 以
      $proxy_add_x_forwarded_for 追加，真实客户端地址位于链尾方向）。
    """
    remote_addr = _normalize_ip(request.META.get("REMOTE_ADDR", "")) or getattr(request, "request_ip", None)
    remote_addr = _normalize_ip(remote_addr)
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if not x_forwarded_for or not remote_addr or not _is_trusted_proxy(remote_addr):
        return remote_addr or "unknown"
    for item in reversed(x_forwarded_for.split(",")):
        ip = _normalize_ip(item)
        if ip and not _is_trusted_proxy(ip):
            return ip
    # 全链均为可信代理：回退直连地址
    return remote_addr or "unknown"


def get_request_data(request):
    """
    获取请求参数
    :param request:
    :return:
    """
    request_data = getattr(request, "request_data", None)
    if request_data:
        return request_data
    if request.META.get("CONTENT_TYPE", "").startswith("multipart/"):
        # multipart（文件上传/表单直传）：只记**字段名清单**，审计日志因此能看到
        # 「提交了哪些字段」（旧实现返回哨兵字符串，日志里没有任何字段信息）。
        #
        # 两条硬约束：
        # ① 不能碰 ``request.POST``——那会触发 Django 自己的 multipart 解析并耗尽请求流，
        #    后续 DRF 只能退回 Django 的 POST/FILES（绕过本仓 AxiosMultiPartParser 的
        #    点号键展开）；
        # ② 不能无条件读 ``request.body``——大文件上传不能整包进内存。
        # 因此仅在 Content-Length ≤ 阈值时读正文（读到的正文被 Django 缓存，DRF 解析
        # 仍走 BytesIO(带点号键展开)），超大请求只留标记。
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            length = 0
        if 0 < length <= MULTIPART_FIELD_PARSE_LIMIT:
            # 前缀断言 [;\s]：避免把 filename="..." 也当成字段名（filename 里含 "name="）
            names = re.findall(rb'[;\s]name="([^"]{1,128})"', request.body)
            fields = sorted({name.decode("utf-8", "ignore") for name in names})
            return {"_multipart_fields": fields}
        return {"_multipart_fields": [], "_multipart_body_skipped": True}
    data: dict = {**request.GET.dict(), **request.POST.dict()}
    if not data:
        try:
            body = request.body
            if body:
                data = json.loads(body)
        except Exception:
            # 请求体非 JSON：按原始载荷处理（日志用途，不阻断请求）
            pass
        if not isinstance(data, dict):
            data = {"data": data}
    return data


def get_request_path(request, *args, **kwargs):
    """
    获取请求路径
    :param request:
    :param args:
    :param kwargs:
    :return:
    """
    request_path = getattr(request, "request_path", None)
    if request_path:
        return request_path
    values = []
    for arg in args:
        if len(arg) == 0:
            continue
        if isinstance(arg, str):
            values.append(arg)
        elif isinstance(arg, (tuple, set, list)):
            values.extend(arg)
        elif isinstance(arg, dict):
            values.extend(arg.values())
    if len(values) == 0:
        return request.path
    path: str = request.path
    for value in values:
        path = path.replace("/" + value, "/" + "{id}")
    return path


def get_user_agent(request):
    """
    解析 User-Agent。每个请求只解析一次（user_agents.parse 是重型正则），
    结果挂在 request 上复用；缺失 UA 头不再抛 KeyError。
    """
    ua_string = request.META.get("HTTP_USER_AGENT", "")
    if getattr(request, "_user_agent_string", None) != ua_string:
        request._parsed_user_agent = parse(ua_string)
        request._user_agent_string = ua_string
    return request._parsed_user_agent


def get_browser(request):
    """
    获取浏览器名
    :param request:
    :return:
    """
    return get_user_agent(request).get_browser()


def get_os(request):
    """
    获取操作系统
    :param request:
    :return:
    """
    return get_user_agent(request).get_os()


def get_verbose_name(queryset=None, view=None, model=None):
    """
    :param model:
    :param queryset:
    :param view:
    :return:
    """
    verbose_name = ""
    try:
        if view is not None and hasattr(view, "__doc__"):
            # docstring 可能多行（如 mfa.UserConfirmViewSet 的 412 交互流程），
            # 操作日志 module 列只有 64 字符且多行文本不可读，统一只取首行
            verbose_name = get_doc_first_line(view.__doc__)
        if queryset is not None and hasattr(queryset, "model"):
            model = queryset.model
        elif view and hasattr(view.get_queryset(), "model"):
            model = view.get_queryset().model
        elif view and hasattr(view.get_serializer(), "Meta") and hasattr(view.get_serializer().Meta, "model"):
            model = view.get_serializer().Meta.model
        if model and not verbose_name:
            verbose_name = model._meta.verbose_name
    except Exception:
        # 视图模型推断不可用：返回调用方传入的缺省值
        pass
    return model, verbose_name


def get_request_ident(request):
    http_user_agent = request.META.get("HTTP_USER_AGENT")
    http_accept = request.META.get("HTTP_ACCEPT")
    remote_addr = BaseThrottle().get_ident(request)
    return base64.b64encode(f"{http_user_agent}{http_accept}{remote_addr}".encode()).decode("utf-8")
