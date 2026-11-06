#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : recovery
"""OTP 恢复码：生成 / 校验 / 一次性消费。

存储口径沿用「hash 后比对」的思路，但摘要版本化（盐 + 慢哈希）——恢复码是
「一次性直到用掉」的持久凭据，不是 90 秒窗口内的防重放标记：

- 明文只在生成响应中出现一次，库内仅存摘要，拖库不可还原；
- 摘要格式 ``pbkdf2$<iter>$<salt_hex>$<hash_hex>``（每码随机盐 + PBKDF2，
  拖库后无法对全库建统一彩虹表）；存量无盐 sha256 行兼容验证，验成功即
  原地升级（lazy rehash），无需停机迁移；
- 验证按用户维度取未用行（≤ 每批 10 行）逐行慢哈希比对：加盐后摘要不可
  预测，无法按摘要精确检索；最坏 10 × PBKDF2（100k 迭代）≈ 数百毫秒，
  恢复码是设备全丢时的低频自救通道，可接受；
- 码面用无易混淆字符的小写字母数字，展示为 5-5 分组；校验前统一
  去分隔符 / 去空白 / 转小写，录入形态不影响命中；
- 消费走条件 UPDATE 原子认领（``used_time IS NULL`` 行数判定），
  并发重放只成功一次；
- 重新绑定 / 重新生成都会整批作废旧码（恢复码是当前 OTP 密钥的配套凭据）。
"""

import hashlib
import hmac
import secrets
from typing import Any

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from mfa.models import MfaRecoveryCode

RECOVERY_CODE_COUNT = 10
_CODE_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"  # 去除 0/o/1/i/l 等易混淆字符
_CODE_LENGTH = 10
_CODE_GROUP = 5

# 版本化摘要参数：100k 迭代是 OWASP 对 PBKDF2-SHA256 的下限推荐；更高迭代
# （如 Django 默认百万级）会让逐行验证的最坏延迟到秒级，自救场景不可接受
_PBKDF2_ITERATIONS = 100_000
_DIGEST_SALT_BYTES = 16
_DIGEST_PREFIX = "pbkdf2$"


def format_code(raw: str) -> str:
    """内部形态 → 展示形态（5-5 分组）"""
    return "-".join(raw[i : i + _CODE_GROUP] for i in range(0, len(raw), _CODE_GROUP))


def normalize_code(value: str) -> str:
    """录入形态 → 内部形态：去分组符 / 空白，转小写"""
    return str(value or "").strip().lower().replace("-", "").replace(" ", "")


def hash_code(raw: str) -> str:
    """生成版本化摘要（随机盐 + PBKDF2，同一明文每次产出不同）"""
    normalized = normalize_code(raw)
    salt = secrets.token_bytes(_DIGEST_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", normalized.encode(), salt, _PBKDF2_ITERATIONS)
    return f"{_DIGEST_PREFIX}{_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def _verify_digest(normalized: str, code_hash: str) -> bool:
    """按摘要格式选算法：``pbkdf2$`` 新格式走慢哈希，无前缀旧格式走 sha256（兼容期）"""
    if code_hash.startswith(_DIGEST_PREFIX):
        try:
            _scheme, iterations, salt_hex, hash_hex = code_hash.split("$", 3)
            digest = hashlib.pbkdf2_hmac("sha256", normalized.encode(), bytes.fromhex(salt_hex), int(iterations))
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(digest.hex(), hash_hex)
    return hmac.compare_digest(hashlib.sha256(normalized.encode()).hexdigest(), code_hash)


def generate_codes(user: Any) -> list[str]:
    """重建该用户的恢复码批次：旧码全部作废，返回明文（仅此一次）"""
    from django.db import transaction

    codes = [
        format_code("".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH)))
        for _ in range(RECOVERY_CODE_COUNT)
    ]
    # 删除与写入必须同事务：中途失败不能留下「无恢复码」的账号
    with transaction.atomic():
        user.mfa_recovery_codes.all().delete()
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
    旧格式（无盐 sha256）验成功后原地升级为新摘要：存量码自然过渡，
    无需停机迁移。
    """
    raw = normalize_code(code)
    generic_failure = _("Recovery code is invalid or has been used")
    if not raw:
        return False, generic_failure
    rows = list(MfaRecoveryCode.objects.filter(user=user, used_time__isnull=True))
    matched = next((row for row in rows if _verify_digest(raw, row.code_hash)), None)
    if matched is None:
        return False, generic_failure
    claimed = MfaRecoveryCode.objects.filter(pk=matched.pk, used_time__isnull=True).update(used_time=timezone.now())
    if not claimed:
        return False, generic_failure
    if not matched.code_hash.startswith(_DIGEST_PREFIX):
        MfaRecoveryCode.objects.filter(pk=matched.pk).update(code_hash=hash_code(code))
    return True, ""
