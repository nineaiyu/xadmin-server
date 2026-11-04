#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""HttpOnly 认证 Cookie 会话（双模式开关）：签发 / 清理 / 读取的唯一实现。

背景：access/refresh token 此前由前端写入 JS 可读 Cookie，
存在 XSS 残余窃取面（读取即外带，refresh 可续 15 天）。本模块把「登录态 token
的服务端 Cookie 承载」收敛为开关控制的**双模式**：

- ``AUTH_COOKIE_HTTPONLY=false``（默认）：零行为变化——token 只出现在响应体，
  前端自行写 Cookie、请求拼 Authorization 头（现状）；
- ``AUTH_COOKIE_HTTPONLY=true``：登录 / 注册 / MFA 验证 / 刷新 / 模拟换签在
  响应头下发 ``HttpOnly; SameSite=Lax; Secure(HTTPS 时)`` 的
  ``X-Token`` / ``X-Refresh-Token``，并**从响应体剥离 token 明文**（键保留、
  值为空串，前端按 ``"access" in data`` 断言登录成功不受影响）；浏览器端
  token 不再可被 JS 读取。Header（Authorization Bearer）与 PAT 认证不受影响
  （认证类双读兼容，回读不设开关）。

模式判定契约（客户端唯一事实源，不靠猜）：

- 服务端在**每次** token 签发响应同时下发 JS 可读的 ``X-Auth-Mode`` 标记
  Cookie（值 ``cookie`` / ``header``，非敏感布尔标记）——两种模式都下发，
  保证开关回退后旧标记不残留误导新会话；
- ``GET /api/identity/auth/capabilities`` 的 ``auth_transport`` 字段是同一开关
  的只读投影（供集成方与登录页预判，见 identity/services/auth_capabilities.py）。

安全口径：

- Cookie 通道写请求的服务端 CSRF 兜底沿用 ``enforce_cookie_csrf``（Origin /
  Referer 同源或白名单，与登录态 API 同一判定）；刷新端点凭 Cookie 取 refresh
  时显式调用；
- 登出**无条件**清理三条 Cookie 并把 Cookie 中的 refresh 拉黑——开关切换窗口
  内的存量会话（旧模式 JS Cookie / 新模式 HttpOnly Cookie）都能登出干净；
- Cookie 寿命比 token 少 10 秒（与前端 setToken 的 ``expires - 10`` 口径一致），
  避免「Cookie 还在、token 已失效」的空转窗口。
"""

from typing import Any

from django.conf import settings

from common.core.auth.authentication import read_auth_cookie
from common.core.response import ApiResponse

#: access token 的 Cookie 名（与前端 js-cookie 的 TokenKey 一致，同名同路径
#: 覆写——开关切换时旧 JS Cookie 被服务端 Cookie 原位替换，无双 Cookie 并存）
AUTH_ACCESS_COOKIE = "X-Token"
#: refresh token 的 Cookie 名
AUTH_REFRESH_COOKIE = "X-Refresh-Token"
#: 承载模式标记 Cookie 名（JS 可读；值 cookie / header）
AUTH_MODE_COOKIE = "X-Auth-Mode"

#: Cookie 寿命相对 token 生命周期的提前过期秒数
COOKIE_LIFETIME_MARGIN_SECONDS = 10


def cookie_session_enabled() -> bool:
    """``AUTH_COOKIE_HTTPONLY`` 开关读取（settings 转发，支持 Setting 表热更）。"""
    return bool(getattr(settings, "AUTH_COOKIE_HTTPONLY", False))


def refresh_token_from_cookie(request: Any) -> str:
    """从 ``X-Refresh-Token`` Cookie 读 refresh token（缺失归一为空串）。

    读取统一经内核 ``read_auth_cookie`` 单一入口（与认证类的 Cookie 回退同一
    解析口径）；供刷新 / 登出 / 退出模拟三个「凭证可能只在 Cookie 里」的端点兜底。
    """
    return read_auth_cookie(request, AUTH_REFRESH_COOKIE)


def _issue_cookie(response: Any, name: str, value: str, max_age: int, secure: bool, httponly: bool) -> None:
    """统一 Cookie 属性下发：``HttpOnly(按需); SameSite=Lax; Secure(HTTPS); Path=/``。"""
    response.set_cookie(
        name,
        value,
        max_age=max(max_age, 0),
        secure=secure,
        httponly=httponly,
        samesite="Lax",
        path="/",
    )


def apply_auth_cookies(request: Any, response: Any, data: dict[str, Any]) -> dict[str, Any]:
    """按开关在 ``response`` 上签发认证 Cookie；返回写入响应体的 data。

    - 关闭：只下发模式标记（``header``），token 留在响应体（零行为变化）；
    - 开启：HttpOnly 签发两条 token Cookie，并把响应体的 ``access`` / ``refresh``
      置空串（键保留：前端登录成功断言按键存在判定）。

    生命周期键缺失或非法时跳过对应 Cookie（不 500），响应体原样透传。
    """
    mode = "cookie" if cookie_session_enabled() else "header"
    secure = bool(request.is_secure())
    access_lifetime = int(data.get("access_token_lifetime") or 0)
    refresh_lifetime = int(data.get("refresh_token_lifetime") or 0)
    # 模式标记两种模式都下发（模块 docstring：开关回退后旧标记不残留）
    _issue_cookie(response, AUTH_MODE_COOKIE, mode, refresh_lifetime - COOKIE_LIFETIME_MARGIN_SECONDS, secure, False)
    if mode == "header":
        return data
    access = str(data.get("access") or "")
    refresh = str(data.get("refresh") or "")
    if access and access_lifetime > COOKIE_LIFETIME_MARGIN_SECONDS:
        _issue_cookie(
            response, AUTH_ACCESS_COOKIE, access, access_lifetime - COOKIE_LIFETIME_MARGIN_SECONDS, secure, True
        )
    if refresh and refresh_lifetime > COOKIE_LIFETIME_MARGIN_SECONDS:
        _issue_cookie(
            response,
            AUTH_REFRESH_COOKIE,
            refresh,
            refresh_lifetime - COOKIE_LIFETIME_MARGIN_SECONDS,
            secure,
            True,
        )
    return {**data, "access": "", "refresh": ""}


def session_token_response(request: Any, data: dict[str, Any], detail: Any = None) -> ApiResponse:
    """登录态 token 响应统一出口：ApiResponse + 按开关签发认证 Cookie。

    所有「把登录态 token 下发给浏览器」的视图（登录 / 注册 / MFA 验证 / 刷新 /
    模拟进出）都必须经本函数返回，不得自行 ``ApiResponse(data=...)`` 后再散写
    Cookie——Cookie 属性（HttpOnly / SameSite / Secure / 寿命）只允许一处定义。
    """
    response = ApiResponse(data=data, detail=detail)
    payload = apply_auth_cookies(request, response, data)
    if payload is not data:
        response.data["data"] = payload
    return response


def clear_auth_cookies(response: Any) -> None:
    """登出清理：无条件清空三条认证 Cookie（path 与签发一致才能命中删除）。

    不设开关：回退窗口内「旧 JS Cookie 会话」与「HttpOnly Cookie 会话」都需要
    在登出时清干净，避免登出后残留可用凭证。
    """
    for name in (AUTH_ACCESS_COOKIE, AUTH_REFRESH_COOKIE, AUTH_MODE_COOKIE):
        response.delete_cookie(name, path="/")
