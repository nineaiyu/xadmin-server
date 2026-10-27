#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""验证码风险档自检的判定口径（doctor 命令与单测共用，纯函数无副作用）。

三类告警的边界：
- 反代部署未配置 ``TRUSTED_PROXY_IPS``：私网兜底会让所有请求共用代理地址，
  风险分级与限流退化成全站单桶；
- enforce 且信任网段为空：办公网可能被误拦；
- 影子期样本不足：直接开 enforce 缺少误拦基线。
"""

from typing import Any


def captcha_risk_findings(
    *,
    mode: str,
    whitelist: list[str],
    trust_private: bool,
    trusted_proxies: list[str],
    samples: int = 0,
    days: int = 0,
) -> list[dict[str, Any]]:
    """返回自检报告项 ``{level, name, detail, fix}``；level 取 ``pass`` / ``warn``。"""
    if mode not in ("shadow", "enforce"):
        return [{"level": "pass", "name": "验证码风险档", "detail": f"{mode}（风险决策未启用）"}]
    findings: list[dict[str, Any]] = []
    if mode == "enforce" and trust_private and not trusted_proxies:
        findings.append(
            {
                "level": "warn",
                "name": "验证码反代",
                "detail": "私网信任已开启，但 TRUSTED_PROXY_IPS 未配置",
                "fix": "反向代理部署请配置 TRUSTED_PROXY_IPS（init_config 会按 compose 子网自动写入）",
            }
        )
    if mode == "enforce" and not whitelist and not trust_private:
        findings.append(
            {
                "level": "warn",
                "name": "验证码风险档",
                "detail": "enforce 且信任网段为空（私网兜底已关闭）",
                "fix": "配置 SECURITY_LOGIN_IP_WHITE_LIST，或在验证码风险策略中开启「信任私网」",
            }
        )
        return findings
    if mode == "enforce" and (samples < 1000 or days < 3):
        findings.append(
            {
                "level": "warn",
                "name": "验证码影子期",
                "detail": f"样本 {samples} / 覆盖 {days} 天（建议 ≥1000 / ≥3 天）",
                "fix": "先切 shadow 积累基线并人工核对 deny 误拦，再开 enforce",
            }
        )
        return findings
    findings.append(
        {
            "level": "pass",
            "name": "验证码风险档",
            "detail": f"{mode}（信任网段 {len(whitelist)} 条 / 私网兜底 {trust_private}）",
        }
    )
    return findings
