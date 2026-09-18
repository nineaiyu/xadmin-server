#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CSP 策略定义（自 server/settings/base.py 拆出，控制单文件体量）。

- 生成方：django-csp（`CONTENT_SECURITY_POLICY` / `..._REPORT_ONLY`）；
- 运行期模式（disabled / report-only / enforce）：CSPModeMiddleware + SysConfig.CSP_MODE；
- 三层同源守护：本文件 ↔ 页面层 nginx 头 ↔ 隔离验证服务，漂移由
  tests/unit/common/test_csp.py 拦截（base.py 继续再导出，守护测试路径不变）。
"""

# CSP 策略（独立于 Office 预览）：
# - style-src 放开 'unsafe-inline'：Element Plus / 图表按需注入内联样式；
# - connect-src 放开 ws:/wss:：应用 WebSocket（应用 ws 与 vite HMR）；
# - img-src 放开 data:/blob:：验证码、预览 blob；
# - frame-src 放开 blob:：Office/PDF 预览内嵌。
_CSP_DIRECTIVES = {
    "default-src": ("'self'",),
    "script-src": ("'self'",),
    # version-rocket 的轮询 Worker 由 Blob 创建（worker-src 未显式声明时回退 script-src，
    # 页面层强制头评估实测被拦）；显式放行同源 + blob: 的最小集合（2026-09-18）
    "worker-src": ("'self'", "blob:"),
    "style-src": ("'self'", "'unsafe-inline'"),
    "img-src": ("'self'", "data:", "blob:"),
    "font-src": ("'self'", "data:"),
    # 图标已离线化（2026-09-18）：菜单/选择器图标随包注册，其余按 set 懒加载**构建期内置**
    # 的图标集（同源 chunk），运行期不访问任何在线图标 API——故不再放行外部主机；
    # 强制头下的「核心页零违规」可反向证明无外部请求（见 e2e/csp-page.e2e.ts）
    "connect-src": ("'self'", "ws:", "wss:"),
    "frame-src": ("'self'", "blob:"),
    "object-src": ("'none'",),
    "base-uri": ("'self'",),
    "form-action": ("'self'",),
    "frame-ancestors": ("'self'",),
}
_CSP_EXCLUDE_PREFIXES = ("/media/", "/api/static/", "/api-docs/")
CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": _CSP_DIRECTIVES,
    "EXCLUDE_URL_PREFIXES": _CSP_EXCLUDE_PREFIXES,
}
CONTENT_SECURITY_POLICY_REPORT_ONLY = {
    "DIRECTIVES": _CSP_DIRECTIVES,
    "EXCLUDE_URL_PREFIXES": _CSP_EXCLUDE_PREFIXES,
}
