#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""风险决策引擎（L1）：可解释规则评分 → 五档决策。

规则分值 / 开关 / 优先级落 ``CaptchaRule``（DB 热更），判据在信号层。
不引入任意表达式求值：每条规则的 code 对应一个已知信号，运营只能调分/开关，
攻击面最小。分数 0~100，永不回显客户端（只入票据与日志）。
"""

from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from captcha import signals as sig
from captcha.const import (
    CAPTCHA_DECISION_ALLOW,
    CAPTCHA_DECISION_CHALLENGE,
    CAPTCHA_DECISION_DENY,
    CAPTCHA_DECISION_HARD,
    CAPTCHA_DECISION_LIGHT,
    current_risk_mode,
)

#: 首批 16 条规则（分值为默认建议，可被 CaptchaRule 行覆盖）
DEFAULT_RULES: tuple[dict[str, Any], ...] = (
    {"code": "env_headless", "name": "Headless browser", "score": 60, "priority": 10},
    {"code": "env_missing", "name": "Missing env payload", "score": 40, "priority": 20},
    {"code": "env_contradiction", "name": "Contradictory env", "score": 25, "priority": 30},
    {"code": "ua_mismatch", "name": "UA / client hints mismatch", "score": 25, "priority": 40},
    {"code": "no_pow", "name": "PoW missing or insufficient", "score": 30, "priority": 50},
    {"code": "track_straight", "name": "Straight pointer path", "score": 25, "priority": 60},
    {"code": "track_uniform", "name": "Uniform pointer speed", "score": 20, "priority": 70},
    {"code": "track_toofast", "name": "Too fast completion", "score": 30, "priority": 80},
    {"code": "track_teleport", "name": "Teleporting pointer", "score": 35, "priority": 90},
    {"code": "freq_ip", "name": "High challenge rate (IP)", "score": 25, "priority": 100},
    {"code": "freq_fp", "name": "Concurrent challenges (fingerprint)", "score": 30, "priority": 110},
    {"code": "acct_fail", "name": "Recent account failures", "score": 30, "priority": 120},
    {"code": "net_datacenter", "name": "Datacenter network", "score": 20, "priority": 130},
    {"code": "net_proxy", "name": "Proxy / TOR exit", "score": 30, "priority": 140},
    {"code": "honeypot", "name": "Honeypot filled", "score": 100, "priority": 1},
    {"code": "hist_good", "name": "Trusted history", "score": -40, "priority": 200},
)
DEFAULT_RULE_CODES = tuple(rule["code"] for rule in DEFAULT_RULES)


@dataclass
class RiskResult:
    score: int
    reasons: list[str] = field(default_factory=list)
    decision: str = CAPTCHA_DECISION_ALLOW
    trusted: bool = False

    @property
    def is_deny(self) -> bool:
        return self.decision == CAPTCHA_DECISION_DENY


def default_rule_rows() -> list[dict[str, Any]]:
    """默认规则行（DB 无记录时兜底；迁移也以此为种子）。"""
    return [
        {
            "code": rule["code"],
            "name": rule["name"],
            "scope": "",
            "score": rule["score"],
            "enabled": True,
            "priority": rule["priority"],
            "params": {},
        }
        for rule in DEFAULT_RULES
    ]


def ensure_default_rules() -> None:
    """幂等补齐 DB 规则行（管理页可改分/关规则；不覆盖已存在行）。"""
    try:
        from captcha.models import CaptchaRule

        existing = set(CaptchaRule.objects.values_list("code", flat=True))
        missing = [row for row in default_rule_rows() if row["code"] not in existing]
        if missing:
            CaptchaRule.objects.bulk_create([CaptchaRule(**row) for row in missing], ignore_conflicts=True)
    except Exception:  # noqa: BLE001 迁移前/DB 不可用：退化为代码默认值
        pass


def load_rules() -> list[dict[str, Any]]:
    """读取启用规则（DB 失败时回退代码默认值，风险层不因 DB 抖动整体失效）。"""
    try:
        from captcha.models import CaptchaRule

        rows = list(CaptchaRule.objects.filter(enabled=True).values("code", "scope", "score", "priority", "params"))
        if rows:
            return sorted(rows, key=lambda row: (row["priority"], row["code"]))
    except Exception:  # noqa: BLE001
        pass
    return [
        {"code": rule["code"], "scope": "", "score": rule["score"], "priority": rule["priority"], "params": {}}
        for rule in DEFAULT_RULES
    ]


def thresholds() -> dict[str, int]:
    return {
        "allow": int(getattr(settings, "CAPTCHA_RISK_T_ALLOW", 20) or 20),
        "light": int(getattr(settings, "CAPTCHA_RISK_T_LIGHT", 40) or 40),
        "hard": int(getattr(settings, "CAPTCHA_RISK_T_HARD", 70) or 70),
        "deny": int(getattr(settings, "CAPTCHA_RISK_T_DENY", 90) or 90),
    }


def decide(score: int, limits: dict[str, int] | None = None) -> str:
    """分数 → 决策；边界语义：达到阈值即进入下一档（20/40/70/90）。"""
    limits = limits or thresholds()
    if score >= limits["deny"]:
        return CAPTCHA_DECISION_DENY
    if score >= limits["hard"]:
        return CAPTCHA_DECISION_HARD
    if score >= limits["light"]:
        return CAPTCHA_DECISION_CHALLENGE
    if score >= limits["allow"]:
        return CAPTCHA_DECISION_LIGHT
    return CAPTCHA_DECISION_ALLOW


def is_trusted_ip(ip: str) -> bool:
    """信任网段：显式白名单 + （默认开启的）私网/回环兜底。"""
    from django.conf import settings as dj_settings

    if sig.in_cidrs(ip, ",".join(getattr(dj_settings, "SECURITY_LOGIN_IP_WHITE_LIST", []) or [])):
        return True
    return bool(getattr(dj_settings, "CAPTCHA_RISK_TRUST_PRIVATE_NETWORKS", True)) and sig.is_private_ip(ip)


def evaluate(hits: dict[str, bool], rules: list[dict[str, Any]] | None = None, scene: str = "") -> RiskResult:
    """规则加权评分；honeypot 命中直接 deny，hist_good 允许负分。"""
    if hits.get("honeypot"):
        return RiskResult(score=100, reasons=["honeypot"], decision=CAPTCHA_DECISION_DENY)
    score = 0
    reasons: list[str] = []
    for rule in rules or load_rules():
        code = str(rule.get("code") or "")
        scope = str(rule.get("scope") or "")
        if not code or not hits.get(code) or (scope and scope != scene):
            continue
        score += int(rule.get("score") or 0)
        reasons.append(code)
    score = max(0, min(score, 100))
    return RiskResult(score=score, reasons=reasons, decision=decide(score))


def collect_hits(
    *,
    env: Any = None,
    ua: str = "",
    accept_language: str = "",
    sec_ch_ua: str = "",
    track: Any = None,
    input_mode: str = "",
    ip: str = "",
    fp: str = "",
    account: str = "",
    pow_required: bool = False,
    pow_ok: bool = False,
    honeypot: bool = False,
    xff_inconsistent: bool = False,
    complex_type: bool = True,
    count_freq: bool = True,
) -> dict[str, bool]:
    """把各层信号汇总为规则 code → 是否命中。"""
    hits = sig.env_signals(env, ua, accept_language, sec_ch_ua)
    metrics = sig.track_metrics(track, input_mode)
    hits.update(sig.track_signals(metrics, complex_type))
    hits.update(
        {
            "no_pow": bool(pow_required and not pow_ok),
            "freq_ip": sig.freq_ip_hit(ip) if count_freq else False,
            "freq_fp": sig.freq_fp_hit(fp) if count_freq else False,
            "acct_fail": sig.account_fail_count(account) >= 3,
            "net_datacenter": sig.in_cidrs(ip, str(getattr(settings, "CAPTCHA_RISK_DATACENTER_CIDRS", "") or "")),
            "net_proxy": xff_inconsistent
            or sig.in_cidrs(ip, str(getattr(settings, "CAPTCHA_RISK_PROXY_CIDRS", "") or "")),
            "honeypot": bool(honeypot and getattr(settings, "CAPTCHA_HONEYPOT_ENABLED", True)),
            "hist_good": sig.hist_good_hit(fp),
        }
    )
    return hits


def assess(
    *,
    scene: str = "",
    ip: str = "",
    **kwargs: Any,
) -> RiskResult:
    """一次完整评估；风险层 off 时由调用方跳过（此函数不自行短路）。"""
    hits = collect_hits(ip=ip, **kwargs)
    if is_trusted_ip(ip):
        return RiskResult(score=0, reasons=["trusted_network"], decision=CAPTCHA_DECISION_ALLOW, trusted=True)
    return evaluate(hits, scene=scene)


def snapshot(result: RiskResult) -> dict[str, Any]:
    """日志/票据用快照（不含任何可回显给客户端的展示语义）。"""
    return {
        "risk_score": result.score,
        "risk_reasons": result.reasons,
        "decision": result.decision,
        "mode": current_risk_mode(),
    }


def severity(decision: str) -> int:
    """决策严格度排序（用于合并挑战期 / 校验期两次评估）。"""
    return {
        CAPTCHA_DECISION_ALLOW: 0,
        CAPTCHA_DECISION_LIGHT: 1,
        CAPTCHA_DECISION_CHALLENGE: 2,
        CAPTCHA_DECISION_HARD: 3,
        CAPTCHA_DECISION_DENY: 4,
    }.get(decision, 0)


def stricter(first: RiskResult, second: RiskResult) -> RiskResult:
    """两次评估取更严格者；同分时保留先出现的原因。"""
    if severity(second.decision) > severity(first.decision):
        return second
    if severity(second.decision) == severity(first.decision) and second.score > first.score:
        return second
    return first
