#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : userinfo
# author : ly_13
# date : 8/10/2024


from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.base.utils import AESCipherV2
from common.core.serializers import BaseModelSerializer
from common.utils import get_logger
from identity import models as identity_models
from identity.models import UserInfo
from identity.serializers.user import (
    PASSWORD_DECRYPT_FAILED_CODE,
    PASSWORD_DECRYPT_FAILED_MESSAGE,
    ensure_local_password_changeable,
)
from settings.services import (
    check_history_password,
    check_leak_password,
    check_password_rules,
    record_password_hash,
)

logger = get_logger(__name__)


class UserInfoSerializer(BaseModelSerializer):
    class Meta:
        model = UserInfo
        write_fields = ["username", "nickname", "gender"]
        fields = write_fields + [
            "email",
            "last_login",
            "pk",
            "phone",
            "avatar",
            "roles",
            "posts",
            "date_joined",
            "dept",
            # 超管标记随本人信息下发：实例评论删除等前端显隐（作者 ∪ 超管）消费
            "is_superuser",
        ]
        read_only_fields = list(set([x.name for x in identity_models.UserInfo._meta.fields]) - set(write_fields))

    dept = serializers.CharField(source="dept.name", read_only=True)
    roles = serializers.SerializerMethodField()
    # 岗位为人员维度标识（不参与权限判定）：仅展示启用岗位，个人中心只读回显
    posts = serializers.SerializerMethodField()

    @extend_schema_field(serializers.ListField)
    def get_roles(self, obj):
        return list(obj.roles.values_list("name", flat=True))

    @extend_schema_field(serializers.ListField)
    def get_posts(self, obj):
        return list(
            obj.posts.filter(is_active=True, deleted_at__isnull=True)
            .order_by("rank", "name")
            .values_list("name", flat=True)
        )


class ChangePasswordSerializer(serializers.Serializer):
    # 密码下限不在字段上硬编码：与改密链路其他序列化器同口径，唯一由
    # check_password_rules 按长度/复杂度开关判定（旧密码仅做核验，不设策略下限）
    old_password = serializers.CharField(max_length=128, required=True, write_only=True, label=_("Old password"))
    sure_password = serializers.CharField(max_length=128, required=True, write_only=True, label=_("Confirm password"))

    def _decrypt_password(self, instance, ciphertext, field):
        """按密文/明文开关解密提交口令；密文模式解密失败拒绝，明文模式回退提交原文。

        前端提交的是 AESCipherV2(username) 加密串；坏 base64 等非法输入不能
        裸抛成 500，须转受控校验异常。
        """
        if settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED:
            try:
                plain = AESCipherV2(instance.username).decrypt(ciphertext)
            except Exception as e:
                logger.warning(f"change password {field} decrypt failed:{e}. rejected")
                raise ValidationError(PASSWORD_DECRYPT_FAILED_MESSAGE, code=PASSWORD_DECRYPT_FAILED_CODE) from e
            if not plain:
                # 解密结果为空 = 密文认证失败（密钥不符/数据被篡改），同解密异常口径拒绝
                raise ValidationError(PASSWORD_DECRYPT_FAILED_MESSAGE, code=PASSWORD_DECRYPT_FAILED_CODE)
            return plain
        # 明文模式（导入/E2E 等场景）：解密失败视为提交值本身是明文，按明文核验
        try:
            return AESCipherV2(instance.username).decrypt(ciphertext)
        except Exception as e:
            logger.warning(f"change password {field} decrypt failed:{e}. fallback to submitted plaintext")
            return ciphertext

    def update(self, instance, validated_data):
        ensure_local_password_changeable(instance)
        old_password = self._decrypt_password(instance, validated_data.get("old_password"), "old_password")
        sure_password = self._decrypt_password(instance, validated_data.get("sure_password"), "sure_password")
        if not instance.check_password(old_password):
            raise serializers.ValidationError(_("Old password verification failed"))
        if not check_password_rules(sure_password, instance.is_superuser):
            raise serializers.ValidationError(_("Password does not match security rules"))
        if check_leak_password(sure_password):
            raise serializers.ValidationError(_("Password has been leaked, please change to another one"))
        if check_history_password(instance, sure_password):
            raise serializers.ValidationError(
                _("Password cannot reuse the recent %(count)s passwords")
                % {"count": settings.SECURITY_PASSWORD_HISTORY_COUNT}
            )

        instance.set_password(sure_password)
        instance.modifier = self.context.get("request").user
        instance.save(update_fields=["password", "modifier"])
        record_password_hash(instance, instance.password)
        return instance
