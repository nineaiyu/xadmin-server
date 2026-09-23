#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""Passkey（WebAuthn）验证后端。

与 OTP / 短信 / 邮件后端的差异：挑战值来自服务端（``/api/mfa/passkey/challenge``，
一次性消费），断言由浏览器 ``navigator.credentials.get`` 产生后以 JSON 串作为
``code`` 提交 —— 因此 ``challenge_required = False``（无需服务端下发验证码）。

同一条 ``check_code`` 链路同时服务登录 MFA 与敏感操作二次确认（412）。
"""

import json

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from mfa.backends.base import BaseMFA
from mfa.const import ConfirmType

logger = get_logger(__name__)


class PasskeyBackend(BaseMFA):
    name = "passkey"
    display_name = _("Passkey")
    placeholder = _("Verify with your passkey")
    # 断言由浏览器完成，服务端无「下发验证码」步骤
    challenge_required = False
    confirm_level = ConfirmType.MFA

    @classmethod
    def global_enabled(cls) -> bool:
        """全局允许方式白名单（SECURITY_MFA_METHODS，空 = 不额外限制）。"""
        methods = [str(item) for item in (getattr(settings, "SECURITY_MFA_METHODS", []) or []) if str(item).strip()]
        return not methods or cls.name in methods

    def is_active(self) -> bool:
        """当前用户至少绑定了一个 Passkey 凭据才可用。"""
        if not getattr(self.user, "pk", None):
            return False
        try:
            return self.user.passkeys.exists()
        except Exception:  # noqa: BLE001 用户未落库等场景视为不可用
            return False

    def check_code(self, code) -> tuple:
        """校验 Passkey 断言（code 为 JSON 串或 dict）。"""
        from system.models import UserPasskey
        from system.utils.webauthn import verify_assertion

        if isinstance(code, str):
            try:
                payload = json.loads(code)
            except Exception:  # noqa: BLE001 客户端数据不可信，归一为校验失败
                return False, str(_("Invalid passkey data"))
        else:
            payload = dict(code or {})
        credential_id = str(payload.get("credential_id") or "")
        if not credential_id:
            return False, str(_("Invalid passkey data"))
        passkey = UserPasskey.objects.filter(user=self.user, credential_id=credential_id).first()
        if passkey is None:
            return False, str(_("This passkey is not bound to the current account"))

        from system.utils.webauthn import rp_id_and_origin

        rp_id, origin = rp_id_and_origin(self.request)
        try:
            sign_count = verify_assertion(
                user=self.user,
                payload=payload,
                public_key=passkey.public_key,
                stored_sign_count=passkey.sign_count,
                expected_rp_id=rp_id,
                expected_origin=origin,
            )
        except ValueError as exc:
            return False, str(exc)
        except Exception:  # noqa: BLE001 校验链路异常归一为失败并告警
            logger.warning("passkey verify failed. user:%s", self.user.pk, exc_info=True)
            return False, str(_("Passkey verification failed"))
        passkey.sign_count = sign_count or passkey.sign_count
        passkey.last_used_at = timezone.now()
        passkey.save(update_fields=["sign_count", "last_used_at", "updated_time"])
        return True, ""
