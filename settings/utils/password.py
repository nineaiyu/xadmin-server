#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : password
# author : ly_13
# date : 8/10/2024
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# 内置离线弱口令清单（常见泄露密码精选；可按需扩充文件行数）
LEAK_PASSWORDS_FILE = Path(__file__).resolve().parent.parent / "data" / "leak_passwords.txt"


@lru_cache(maxsize=1)
def _load_leak_passwords() -> frozenset:
    """加载内置泄露密码库（一行一条，进程内缓存；文件缺失视为空库）"""
    try:
        lines = LEAK_PASSWORDS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return frozenset()
    return frozenset(line.strip() for line in lines if line.strip())


def check_leak_password(password: str) -> bool:
    """命中泄露密码库返回 True。开关（SECURITY_PASSWORD_LEAK_CHECK_ENABLED）关闭时恒 False。

    离线库比对（不联网查询），精确匹配；接入点：本人改密 / 管理端建号重置 / 注册 / 忘记密码重置。
    """
    if not settings.SECURITY_PASSWORD_LEAK_CHECK_ENABLED:
        return False
    return password in _load_leak_passwords()


def check_history_password(user, password: str) -> bool:
    """命中用户最近 N 次历史密码返回 True。N（SECURITY_PASSWORD_HISTORY_COUNT）<= 0 时恒 False。

    逐条与历史哈希做 check_password 比对（哈希盐随机，无法等值查询）。
    """
    count = int(settings.SECURITY_PASSWORD_HISTORY_COUNT or 0)
    if count <= 0 or user is None or user.pk is None:
        return False
    histories = user.password_histories.all()[:count]
    for history in histories:
        if check_password(password, history.password):
            return True
    return False


def record_password_hash(user, hashed_password: str) -> None:
    """改密成功后留存密码哈希并刷新 date_password_updated（密码过期计时起点）。

    各改密链路（本人改密 / 管理端重置 / 忘记密码重置 / 注册 / 管理端建号）在
    instance.save() 之后调用；失败不影响主流程，绝不阻断登录态。
    """
    if user is None or user.pk is None or not hashed_password:
        return
    from common.utils import get_logger
    from system.models.password import PasswordHistory

    logger = get_logger(__name__)
    try:
        PasswordHistory.objects.create(user=user, password=hashed_password, creator=user)
    except Exception:
        # 历史留存失败仅告警，不阻断改密主流程
        logger.warning("record password history failed. user: %s", user)
    # 密码过期计时起点；逐用户低频写（仅改密时发生），与改密主 save 分离避免字段覆盖
    user.date_password_updated = timezone.now()
    user.save(update_fields=["date_password_updated"])


def is_password_expired(user) -> bool:
    """密码是否已过期（超过 SECURITY_PASSWORD_EXPIRATION_DAYS 天未更新）。

    天数 <= 0 = 永不过期；date_password_updated 为空 = 未跟踪（存量宽限期，不拦截）。
    """
    days = int(settings.SECURITY_PASSWORD_EXPIRATION_DAYS or 0)
    if days <= 0:
        return False
    if user is None or getattr(user, "date_password_updated", None) is None:
        return False
    return timezone.now() - user.date_password_updated > timezone.timedelta(days=days)


PASSWORD_EXPIRED_MESSAGE = _("Password has expired, please change your password before logging in")


def get_password_check_rules(user):
    check_rules = []
    for rule in settings.SECURITY_PASSWORD_RULES:
        if user.is_superuser and rule == "SECURITY_PASSWORD_MIN_LENGTH":
            rule = "SECURITY_ADMIN_USER_PASSWORD_MIN_LENGTH"
        value = getattr(settings, rule)
        if not value:
            continue
        check_rules.append({"key": rule, "value": int(value)})
    return check_rules


def check_password_rules(password, is_super_admin=False):
    pattern = r"^"
    if settings.SECURITY_PASSWORD_UPPER_CASE:
        pattern += r"(?=.*[A-Z])"
    if settings.SECURITY_PASSWORD_LOWER_CASE:
        pattern += r"(?=.*[a-z])"
    if settings.SECURITY_PASSWORD_NUMBER:
        pattern += r"(?=.*\d)"
    if settings.SECURITY_PASSWORD_SPECIAL_CHAR:
        pattern += r'(?=.*[`~!@#$%^&*()\-=_+\[\]{}|;:\'",.<>/?])'
    pattern += r"[a-zA-Z\d`~!@#\$%\^&\*\(\)-=_\+\[\]\{\}\|;:\'\",\.<>\/\?]"
    if is_super_admin:
        min_length = settings.SECURITY_ADMIN_USER_PASSWORD_MIN_LENGTH
    else:
        min_length = settings.SECURITY_PASSWORD_MIN_LENGTH
    pattern += ".{" + str(min_length - 1) + ",}$"
    match_obj = re.match(pattern, password)
    return bool(match_obj)
