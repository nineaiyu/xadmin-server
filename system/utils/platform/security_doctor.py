#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""安全基线三态自检条目（doctor 消费；口径与 captcha_doctor 同构）。

三项均为「默认取兼容口径、由部署按风险面收紧」的三态设计（审查修复
SEC-L1 / SEC-L2 / SEC-L3 的部署侧落实项）：默认值对内网同源部署合理，但公网 /
跨域 / 高安全部署需要显式收紧——本自检把「默认偏宽松的组合」点名告警
（只告警不判失败，收紧动作留给部署方），并对已收紧的部署给出通过：
1. CSRF_REQUIRE_ORIGIN：Cookie 通道非安全方法在 Origin/Referer 双缺失时的兜底
   （Bearer/PAT 通道不受影响；默认关闭保持 CLI/脚本直调兼容）；
2. SECURITY_HTTPS_ENABLED：HSTS + Session/CSRF Cookie Secure（默认关闭 =
   HTTP 直连零影响；「HTTP 直连 + 文档站会话」的组合风险面在此点名）；
3. CSP_MODE：Django 侧 API 响应的 CSP 运行期档位（页面层已由 nginx 强制）。
"""

from typing import Any

from django.conf import settings

PASS = "✅"
WARN = "⚠️"


def _csrf_finding() -> dict[str, Any]:
    if getattr(settings, "DEBUG", False):
        return {"level": "pass", "name": "安全基线", "detail": "CSRF Origin 兜底：开发态（DEBUG）豁免"}
    if getattr(settings, "CSRF_REQUIRE_ORIGIN", False):
        return {"level": "pass", "name": "安全基线", "detail": "CSRF Origin 兜底已开启（Cookie 通道写请求双缺失即拒）"}
    return {
        "level": "warn",
        "name": "安全基线",
        "detail": "CSRF_REQUIRE_ORIGIN 未开启：Cookie 通道写请求在 Origin/Referer 双缺失时放行"
        "（现代浏览器不受影响，面覆盖非浏览器客户端）",
        "fix": "跨域/公网/高安全部署在 config.yml 配置 CSRF_REQUIRE_ORIGIN: true",
    }


def _https_finding() -> dict[str, Any]:
    if getattr(settings, "DEBUG", False):
        return {"level": "pass", "name": "安全基线", "detail": "HTTPS 安全头：开发态（DEBUG）豁免"}
    if getattr(settings, "SECURITY_HTTPS_ENABLED", False):
        return {"level": "pass", "name": "安全基线", "detail": "HTTPS 安全头已开启（HSTS + Secure Cookie）"}
    return {
        "level": "warn",
        "name": "安全基线",
        "detail": "SECURITY_HTTPS_ENABLED 未开启：无 HSTS，文档站（/api-docs）会话 Cookie 不带 Secure"
        "——HTTP 直连部署的链路嗅探风险面",
        "fix": "公网部署开启 SECURITY_HTTPS_ENABLED: true（或网关终结 TLS 后开启）",
    }


def _csp_finding() -> dict[str, Any]:
    try:
        from common.core.config import SysConfig

        mode = str(getattr(SysConfig, "CSP_MODE", None) or "report-only").strip().lower()
    except Exception:  # noqa: BLE001 库未就绪（迁移前）时按缺省口径评估
        mode = "report-only"
    if mode == "enforce":
        return {"level": "pass", "name": "安全基线", "detail": "CSP 运行期档位：enforce（Django 侧响应强制）"}
    if mode == "disabled":
        return {
            "level": "warn",
            "name": "安全基线",
            "detail": "CSP 运行期档位：disabled（Django 侧响应不下发 CSP 头）",
            "fix": "页面层已由 nginx 强制；Django 侧建议按灰度流程恢复 report-only → enforce",
        }
    return {
        "level": "warn",
        "name": "安全基线",
        "detail": "CSP 运行期档位：report-only（观察期，Django 侧响应不下发强制头）",
        "fix": "隔离验证无违规后经系统配置切 enforce（灰度流程见 docs/ops/deployment-csp.md）",
    }


def security_posture_findings() -> list[dict[str, Any]]:
    """安全基线自检条目（pass/warn，消费口径与 captcha_risk_findings 一致）。"""
    return [_csrf_finding(), _https_finding(), _csp_finding()]


def report_security_posture(report: Any) -> None:
    """输出安全基线自检（report = doctor 的 ``self._report``；单项异常不中断整体）。"""
    try:
        for item in security_posture_findings():
            mark = PASS if item["level"] == "pass" else WARN
            report(mark, item["name"], item["detail"], item.get("fix"))
    except Exception as exc:  # noqa: BLE001 自检项不因单项异常中断整体
        report(WARN, "安全基线", f"检查失败（跳过）：{exc}")
