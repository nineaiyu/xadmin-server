#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""settings 静态断言：把"配置组合"类架构决策固化为可回归的测试。

修订：启用 django.contrib.admin 时必须存在 CsrfViewMiddleware。
后续同类断言（如 SECRET_KEY 拒启、SILK_ENABLED 仅限 DEBUG）可继续沉淀在本文件。
"""


def test_csrf_middleware_required_when_admin_enabled():
    """修订：/admin/ 站点依赖 Session+CSRF，启用 Admin 时禁止移除 CSRF 中间件。"""
    from django.conf import settings

    middleware = list(settings.MIDDLEWARE)
    if 'django.contrib.admin' in settings.INSTALLED_APPS:
        assert 'django.middleware.csrf.CsrfViewMiddleware' in middleware, (
            'django.contrib.admin 已启用（/admin/ 依赖 Session+CSRF），'
            '禁止移除 CsrfViewMiddleware——若确需移除，请先关闭 Admin 站点'
        )


def test_csrf_middleware_position():
    """CSRF 中间件必须位于 SessionMiddleware（获取会话）之后、AuthenticationMiddleware（其依赖 csrf 校验语境）之前。"""
    from django.conf import settings

    middleware = settings.MIDDLEWARE
    csrf_idx = middleware.index('django.middleware.csrf.CsrfViewMiddleware')
    assert csrf_idx > middleware.index('django.contrib.sessions.middleware.SessionMiddleware')
    assert csrf_idx < middleware.index('django.contrib.auth.middleware.AuthenticationMiddleware')
