#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : recovery
"""OTP 恢复码：生成 / 校验 / 一次性消费。

存储口径沿用 UsedOtpCodeCache 的哈希模式（hash 后比对），但落库而非 Redis——
恢复码是「一次性直到用掉」的持久凭据，不是 90 秒窗口内的防重放标记：

- 明文只在生成响应中出现一次，库内仅存 sha256 摘要，拖库不可还原；
- 码面用无易混淆字符的小写字母数字，展示为 5-5 分组；校验前统一
  去分隔符 / 去空白 / 转小写，录入形态不影响命中；
- 消费走条件 UPDATE 原子认领（``used_time IS NULL`` 行数判定），
  并发重放只成功一次；
- 重新绑定 / 重新生成都会整批作废旧码（恢复码是当前 OTP 密钥的配套凭据）。
"""

import hashlib
import secrets
from typing import Any

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from mfa.models import MfaRecoveryCode

RECOVERY_CODE_COUNT = 10
_CODE_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"  # 去除 0/o/1/i/l 等易混淆字符
_CODE_LENGTH = 10
_CODE_GROUP = 5


def format_code(raw: str) -> str:
    """内部形态 → 展示形态（5-5 分组）"""
    return "-".join(raw[i : i + _CODE_GROUP] for i in range(0, len(raw), _CODE_GROUP))


def normalize_code(value: str) -> str:
    """录入形态 → 内部形态：去分组符 / 空白，转小写"""
    return str(value or "").strip().lower().replace("-", "").replace(" ", "")


def hash_code(raw: str) -> str:
    return hashlib.sha256(normalize_code(raw).encode()).hexdigest()


def generate_codes(user: Any) -> list[str]:
    """重建该用户的恢复码批次：旧码全部作废，返回明文（仅此一次）"""
    user.mfa_recovery_codes.all().delete()
    codes = [
        format_code("".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH)))
        for _ in range(RECOVERY_CODE_COUNT)
    ]
    MfaRecoveryCode.objects.bulk_create([MfaRecoveryCode(user=user, code_hash=hash_code(code)) for code in codes])
    return codes


def remaining_count(user: Any) -> int:
    count: int = user.mfa_recovery_codes.filter(used_time__isnull=True).count()
    return count


def clear_codes(user: Any) -> None:
    """作废该用户全部恢复码（解绑 / 管理员重置时同步调用：密钥不在即无意义）"""
    user.mfa_recovery_codes.all().delete()


def verify_and_consume(user: Any, code: str) -> tuple[bool, Any]:
    """校验并原子认领一个恢复码，返回 (是否通过, 失败原因)

    无效与已使用同文案，不暴露「该码存在但已用」的判定面。
    """
    raw = normalize_code(code)
    if not raw:
        return False, _("Recovery code is invalid or has been used")
    claimed = MfaRecoveryCode.objects.filter(user=user, code_hash=hash_code(raw), used_time__isnull=True).update(
        used_time=timezone.now()
    )
    if not claimed:
        return False, _("Recovery code is invalid or has been used")
    return True, ""
