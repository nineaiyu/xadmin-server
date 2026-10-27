#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""验证码风险信号提取（L0）：客户端环境包 / 轨迹 / 服务端旁路信号。

所有函数都是纯函数或只读缓存查询，不产生副作用；信号一律映射为布尔命中，
由 ``captcha/risk.py`` 按 DB 规则表加权，保证「可解释、可单独关闭、可改分」。
隐私口径：只处理摘要与类别，不落明文 IP / UA / 账号。
"""

import hashlib
import ipaddress
import json
import re
from typing import Any

from django.conf import settings
from django.core.cache import cache

ENV_VERSION = 1
#: 挑战速率 / 指纹并发 / 历史信誉的缓存前缀（TTL 见各函数）
FREQ_IP_PREFIX = "captcha_freq_ip"
FREQ_FP_PREFIX = "captcha_freq_fp"
HIST_PREFIX = "captcha_hist"
ACCT_FAIL_PREFIX = "captcha_acct_fail"

_MOBILE_RE = re.compile(r"Mobile|Android|iPhone|iPad|iPod", re.I)
_CHROME_RE = re.compile(r"Chrome/|Chromium/|Edg/|OPR/", re.I)


def fp_salt() -> str:
    """指纹/网段哈希盐：显式配置优先，否则由票据密钥派生（换盐即断历史关联）。"""
    configured = str(getattr(settings, "CAPTCHA_FP_SALT", "") or "").strip()
    if configured:
        return configured
    from captcha.tickets import _derived_secret

    return _derived_secret()


def hash_value(value: str) -> str:
    """加盐 SHA-256；空值返回空串（不产生可关联的固定哈希）。"""
    value = str(value or "").strip()
    if not value:
        return ""
    return hashlib.sha256(f"{fp_salt()}|{value}".encode()).hexdigest()


def hash_ip(ip: str) -> str:
    """IP 按 /24（IPv4）或 /64（IPv6）前缀哈希：保留网段信号，不可反推个体。"""
    ip = str(ip or "").strip()
    if not ip:
        return ""
    try:
        addr = ipaddress.ip_address(ip)
        network = (
            ipaddress.ip_network(f"{addr}/24", strict=False)
            if addr.version == 4
            else ipaddress.ip_network(f"{addr}/64", strict=False)
        )
        return hash_value(str(network))
    except ValueError:
        return hash_value(ip)


def fingerprint(env: Any, ua: str = "") -> str:
    """客户端指纹 = 环境摘要 + UA 大类的哈希（不含明文）。"""
    if not isinstance(env, dict):
        return hash_value(ua)
    parts = [
        str(env.get("timezone") or ""),
        str(env.get("language") or ""),
        str((env.get("screen") or {}).get("width") or ""),
        str((env.get("screen") or {}).get("height") or ""),
        str((env.get("screen") or {}).get("dpr") or ""),
        str(env.get("cores") or ""),
        str(env.get("touch") or ""),
        str(env.get("canvas") or ""),
        str(env.get("webgl") or ""),
        classify_ua(ua),
    ]
    return hash_value("|".join(parts))


def classify_ua(ua: str) -> str:
    if not ua:
        return "other"
    if re.search(r"Headless", ua, re.I):
        return "headless"
    if _MOBILE_RE.search(ua):
        return "mobile"
    if "Mozilla" in ua or "Chrome" in ua or "Safari" in ua:
        return "desktop"
    return "other"


def _primary_language(value: str) -> str:
    return str(value or "").split(",")[0].split(";")[0].strip().lower().replace("_", "-")


def env_signals(env: Any, ua: str = "", accept_language: str = "", sec_ch_ua: str = "") -> dict[str, bool]:
    """环境包与服务端旁路的一致性信号。"""
    data = env if isinstance(env, dict) else {}
    raw_screen = data.get("screen")
    screen: dict[str, Any] = raw_screen if isinstance(raw_screen, dict) else {}
    language = str(data.get("language") or "")
    timezone_name = str(data.get("timezone") or "")
    width = int(screen.get("width") or 0)
    height = int(screen.get("height") or 0)
    dpr = float(screen.get("dpr") or 0)
    contradiction = False
    try:
        from zoneinfo import ZoneInfo

        if timezone_name:
            ZoneInfo(timezone_name)
    except Exception:  # noqa: BLE001 非法时区即矛盾
        contradiction = True
    if (width and not 320 <= width <= 10000) or (height and not 240 <= height <= 10000):
        contradiction = True
    if dpr and not 0.5 <= dpr <= 5:
        contradiction = True
    client_lang = _primary_language(language)
    server_lang = _primary_language(accept_language)
    if client_lang and server_lang and client_lang != server_lang:
        contradiction = True
    return {
        "env_missing": data.get("v") != ENV_VERSION,
        "env_headless": bool(data.get("webdriver")) or bool(re.search(r"Headless", ua or "", re.I)),
        "env_contradiction": contradiction,
        "ua_mismatch": bool(_CHROME_RE.search(ua or "")) and not bool(sec_ch_ua),
    }


def is_headless(ua: str) -> bool:
    return bool(re.search(r"Headless", ua or "", re.I))


def in_cidrs(ip: str, raw: str) -> bool:
    """IP 是否命中逗号分隔 CIDR 清单（空清单恒 False）。"""
    ip = str(ip or "").strip()
    if not ip or not raw:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for item in str(raw).split(","):
        item = item.strip()
        if not item:
            continue
        try:
            if addr in ipaddress.ip_network(item, strict=False):
                return True
        except ValueError:
            continue
    return False


def xff_inconsistent(meta: Any) -> bool:
    """X-Forwarded-For 链路一致性（仅在配置了可信代理清单时判定，避免误伤）。

    - 未配置 ``TRUSTED_PROXY_IPS``：无法判断，恒 False；
    - 直连地址不在可信代理清单却携带 XFF：头不可信（伪造）；
    - 可信代理转发但链中存在非法地址：链路异常。
    """
    xff = str((meta or {}).get("HTTP_X_FORWARDED_FOR") or "").strip()
    if not xff:
        return False
    from common.settings_contract import kernel_setting

    trusted = kernel_setting("TRUSTED_PROXY_IPS") or []
    if not trusted:
        return False
    remote = str((meta or {}).get("REMOTE_ADDR") or "").strip()
    try:
        trusted_networks = [ipaddress.ip_network(str(item), strict=False) for item in trusted]
        remote_addr = ipaddress.ip_address(remote)
    except ValueError:
        return True
    if not any(remote_addr in network for network in trusted_networks):
        return True
    for hop in xff.split(","):
        try:
            ipaddress.ip_address(hop.strip())
        except ValueError:
            return True
    return False


def is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(str(ip or "").strip())
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback


def track_metrics(track: Any, input_mode: str = "") -> dict[str, Any]:
    """轨迹特征：直线度 / 速度方差 / 时长 / 最大速度；键盘操作单独标记。"""
    points = [p for p in (track or []) if isinstance(p, dict)]
    result: dict[str, Any] = {
        "points": len(points),
        "duration_ms": 0,
        "straightness": 0.0,
        "speed_variance": 1.0,
        "max_speed": 0.0,
        "keyboard": str(input_mode or "").lower() == "keyboard",
    }
    if len(points) < 2:
        return result
    try:
        times = [float(p.get("t") or 0) for p in points]
        xs = [float(p.get("x") or 0) for p in points]
        ys = [float(p.get("y") or 0) for p in points]
    except (TypeError, ValueError):
        return result
    duration = max(times) - min(times)
    path = sum(((xs[i + 1] - xs[i]) ** 2 + (ys[i + 1] - ys[i]) ** 2) ** 0.5 for i in range(len(points) - 1))
    displacement = ((xs[-1] - xs[0]) ** 2 + (ys[-1] - ys[0]) ** 2) ** 0.5
    speeds = []
    for i in range(len(points) - 1):
        dt = max(times[i + 1] - times[i], 1.0)
        speeds.append(((xs[i + 1] - xs[i]) ** 2 + (ys[i + 1] - ys[i]) ** 2) ** 0.5 / dt)
    mean = sum(speeds) / len(speeds) if speeds else 0.0
    variance = sum((s - mean) ** 2 for s in speeds) / len(speeds) if speeds else 0.0
    result.update(
        duration_ms=int(duration),
        straightness=round(displacement / path, 4) if path else 0.0,
        speed_variance=round(variance, 4),
        max_speed=round(max(speeds) if speeds else 0.0, 4),
    )
    return result


def track_signals(metrics: dict[str, Any], complex_type: bool = True) -> dict[str, bool]:
    """轨迹规则命中；键盘路径不参与任何「像脚本」的扣分。"""
    if metrics.get("keyboard"):
        return {"track_straight": False, "track_uniform": False, "track_toofast": False, "track_teleport": False}
    return {
        "track_straight": float(metrics.get("straightness") or 0) > 0.98,
        "track_uniform": metrics.get("speed_variance") is not None and float(metrics["speed_variance"]) < 0.5,
        "track_toofast": complex_type and 0 < int(metrics.get("duration_ms") or 0) < 400,
        "track_teleport": float(metrics.get("max_speed") or 0) > 50,
    }


def freq_ip_hit(ip: str, limit: int = 10, window: int = 60) -> bool:
    """同 IP 挑战速率：挑战入口自增，超过窗口阈值即命中。"""
    key = f"{FREQ_IP_PREFIX}_{hash_ip(ip)}"
    try:
        count = int(cache.get(key, 0) or 0) + 1
        cache.set(key, count, window)
        return count > limit
    except Exception:  # noqa: BLE001 缓存不可用 = 无法判定（不误伤）
        return False


def freq_fp_hit(fp: str, limit: int = 3, window: int = 60) -> bool:
    key = f"{FREQ_FP_PREFIX}_{fp}"
    if not fp:
        return False
    try:
        count = int(cache.get(key, 0) or 0) + 1
        cache.set(key, count, window)
        return count > limit
    except Exception:  # noqa: BLE001
        return False


def account_fail_count(account: str) -> int:
    if not account:
        return 0
    try:
        return int(cache.get(f"{ACCT_FAIL_PREFIX}_{hash_value(account)}", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


def record_account_fail(account: str, window: int = 900) -> None:
    if not account:
        return
    key = f"{ACCT_FAIL_PREFIX}_{hash_value(account)}"
    try:
        cache.set(key, int(cache.get(key, 0) or 0) + 1, window)
    except Exception:  # noqa: BLE001
        pass


def hist_good_hit(fp: str) -> bool:
    """历史信誉：同指纹 7 天内成功 ≥3 且无失败（负分项）。"""
    if not fp:
        return False
    try:
        data = cache.get(f"{HIST_PREFIX}_{fp}") or {}
        return int(data.get("success", 0) or 0) >= 3 and int(data.get("fail", 0) or 0) == 0
    except Exception:  # noqa: BLE001
        return False


def record_hist(fp: str, success: bool, window: int = 7 * 24 * 3600) -> None:
    if not fp:
        return
    key = f"{HIST_PREFIX}_{fp}"
    try:
        data = dict(cache.get(key) or {})
        data["success" if success else "fail"] = int(data.get("success" if success else "fail", 0)) + 1
        cache.set(key, data, window)
    except Exception:  # noqa: BLE001
        pass


def env_version(env: Any) -> str:
    if not isinstance(env, dict):
        return ""
    return str(env.get("v") or "")


def dump_digest(value: Any) -> str:
    """调试/测试用的稳定摘要（不落原始环境包）。"""
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]
