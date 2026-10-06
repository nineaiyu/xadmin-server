#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : user
# author : ly_13
# date : 8/10/2024

import json

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.validators import UniqueValidator

from common.base.utils import AESCipherV2
from common.core.fields import DictChoiceField
from common.core.filter import assert_within_data_scope
from common.core.serializers import BaseModelSerializer
from common.fields.utils import input_wrapper
from common.utils import get_logger
from identity.models import DeptInfo, UserInfo, UserRole
from identity.models.ldap import LdapUserBinding
from identity.utils import user_invite
from message.services import get_online_users_layers
from settings.services import (
    LoginBlockUtil,
    check_history_password,
    check_leak_password,
    check_password_rules,
    record_password_hash,
)
from system.services import DataPermission, TaggedObjectSerializerMixin

logger = get_logger(__name__)

# 建号密码解密失败的统一报错口径：密文模式下前端提交的是加密串，解密失败
# （密钥不符/数据被篡改/提交了明文）一律拒绝，不把密文/明文误当密码落库。
# code 供视图层识别该类拒绝（先落审计再以业务码应答，避免审计随请求事务回滚丢失）
PASSWORD_DECRYPT_FAILED_MESSAGE = _("Password decryption failed, please refresh the page and try again")
PASSWORD_DECRYPT_FAILED_CODE = "password_decrypt_failed"


def is_password_decrypt_failure(exc) -> bool:
    """判断校验异常是否为建号密码解密失败（按 ErrorDetail.code 识别，与文案解耦）。"""
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        items = [item for value in detail.values() if isinstance(value, list) for item in value]
    elif isinstance(detail, list):
        items = detail
    else:
        items = [detail]
    return any(getattr(item, "code", None) == PASSWORD_DECRYPT_FAILED_CODE for item in items)


def record_create_password_decrypt_failure(request, username):
    """建号密码解密失败的补充审计：落 OperationLog 供安全追溯。

    与 SCIM 目录同步的操作审计同口径（module 打域内标签、changes 记结构化摘要、
    不携带提交的密码原文、审计失败仅告警不阻断主流程）。创建被拒时对象不存在，
    object_pk 留空，以 changes.username 定位目标账号。
    """
    from audit.services import OperationLog
    from common.utils.request import get_request_ip

    try:
        user = getattr(request, "user", None)
        OperationLog.objects.create(
            module="identity:user",
            path=(getattr(request, "path", "") or "")[:400],
            method=(getattr(request, "method", "") or "")[:8],
            ipaddress=get_request_ip(request),
            creator=user if getattr(user, "pk", None) else None,
            status_code=1001,
            response_code=1001,
            changes=json.dumps(
                {"action": "create", "error": "password_decrypt_failed", "username": str(username or "")[:64]},
                ensure_ascii=False,
            )[:4096],
        )
    except Exception:  # noqa: BLE001 审计链路故障不影响主流程
        logger.warning("record create password decrypt failure audit failed", exc_info=True)


def ensure_local_password_changeable(user):
    """LDAP 绑定用户拒绝本地改密/重置：密码由目录服务器管理。"""
    if LdapUserBinding.objects.filter(user=user).exists():
        raise ValidationError(_("Password is managed by the LDAP directory and cannot be changed locally"))


class UserSerializer(TaggedObjectSerializerMixin, BaseModelSerializer):
    # gender 下拉由数据字典驱动（字典类型 user_gender）：标签/选项在字典页维护即时生效；
    # 字典未配置时回退模型 GenderChoices，value 回调整型适配 IntegerField
    gender = DictChoiceField(
        dict_code="user_gender",
        value_cast=int,
        fallback_choices=UserInfo.GenderChoices.choices,
        required=False,  # 模型 default=UNKNOWN 兜底；显式声明不继承 build_standard_field 的 default
        label=_("Gender"),
    )
    # 通用标签：只读回显（打标走 /api/system/tags/assign，权限回落 update 权限点）
    tags = serializers.SerializerMethodField(label=_("Tags"))
    # 创建即邀请：write_only 开关（创建后由服务端置待激活 + 发邀请邮件，无需密码）
    invite = serializers.BooleanField(write_only=True, required=False, default=False, label=_("Invite activation"))

    class Meta:
        model = UserInfo
        fields = [
            "pk",
            "avatar",
            "username",
            "nickname",
            "phone",
            "email",
            "gender",
            "block",
            "online_count",
            "is_active",
            "password",
            "dept",
            "description",
            "last_login",
            "date_joined",
            "date_expired",
            "invite_status",
            "invited_time",
            "is_superuser",
            "roles",
            "posts",
            "rules",
            "tags",
            "invite",
            "deleted_at",
        ]
        read_only_fields = ["pk", "deleted_at"] + list(set([x.name for x in UserInfo._meta.fields]) - set(fields))
        table_fields = [
            "pk",
            "avatar",
            "username",
            "nickname",
            "gender",
            "block",
            "online_count",
            "is_active",
            "invite_status",
            "dept",
            "phone",
            "date_expired",
            "last_login",
            "date_joined",
            "roles",
            "posts",
            "rules",
            "tags",
        ]
        extra_kwargs = {
            "pk": {"read_only": True},
            # 仅随行下发供前端行级显隐（隐藏不可模拟的超级管理员），写入口在管理命令
            "is_superuser": {"read_only": True},
            "last_login": {"read_only": True},
            "date_joined": {"read_only": True},
            "avatar": {"read_only": True},
            "password": {"write_only": True},
            # 邀请状态与邀请时间为服务端维护（写入口 = invite action），只读回显
            "invite_status": {"read_only": True},
            "invited_time": {"read_only": True},
            "roles": {"required": False, "attrs": ["pk", "name", "code"], "format": "{name}", "many": True},
            # 岗位与角色同口径：用户表单内可直接编辑（多选），与岗位页成员分配互为补充
            "posts": {"required": False, "attrs": ["pk", "name", "code"], "format": "{name}", "many": True},
            "rules": {
                "required": False,
                "attrs": ["pk", "name", "get_mode_type_display"],
                "format": "{name}",
                "many": True,
            },
            "dept": {"required": False, "attrs": ["pk", "name", "parent_id"], "format": "{name}"},
            "email": {"validators": [UniqueValidator(queryset=UserInfo.objects.all())]},
            "phone": {"validators": [UniqueValidator(queryset=UserInfo.objects.all())]},
        }

    block = input_wrapper(serializers.SerializerMethodField)(
        read_only=True, input_type="boolean", label=_("Login blocked")
    )
    online_count = input_wrapper(serializers.SerializerMethodField)(
        read_only=True, input_type="number", label=_("Online count")
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 创建即邀请：邀请模式无需密码（由被邀请人自行设置）→ 放开字段级必填
        request = self.context.get("request")
        if user_invite.invite_requested(getattr(request, "data", None)):
            self.fields["password"].required = False

    # username 在 DB 层保持全局唯一（auth.E003 约束 USERNAME_FIELD 必须 unique），
    # 模型字段 unique=True 使 DRF 自动生成的 UniqueValidator 只查活跃数据（默认管理器），
    # 会放过回收站中的同名用户造成 IntegrityError——这里显式按 all_objects 拦截，
    # 回收站用户名视为占用并返回可读 400
    def validate_username(self, value):
        queryset = UserInfo.all_objects.filter(username=value)
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise ValidationError(_("This field already exists"))
        return value

    @extend_schema_field(serializers.BooleanField)
    def get_block(self, obj):
        # 以整页用户名为单位批量查询锁定状态，结果缓存在 context 中（ListSerializer 与子字段共享）
        if "user_login_block" not in self.context:
            usernames = [instance.username for instance in self.get_page_instances(obj)]
            self.context["user_login_block"] = LoginBlockUtil.get_users_block(usernames)
        return self.context["user_login_block"].get(obj.username, False)

    @extend_schema_field(serializers.IntegerField)
    def get_online_count(self, obj):
        if "user_online_layers" not in self.context:
            pks = [instance.pk for instance in self.get_page_instances(obj)]
            self.context["user_online_layers"] = get_online_users_layers(pks)
        return len(self.context["user_online_layers"].get(obj.pk, []))

    def _assert_scope_fields(self, attrs):
        """写侧载荷范围校验：归属字段与关系字段必须落在可授权范围内。

        与读侧同源（数据权限可见范围 = 可写范围）：
        - ``dept``：非超管指定的目标部门须可见；把已有归属清空（挪出管辖）同样拒绝，
          原本就无归属（原值为空）时放行（避免「编辑无部门用户」被误伤）；
        - ``roles`` / ``rules``：赋值面须全部在可授权池内（与 empower 同口径，逐项
          校验而非静默丢弃）；空值/未提交不校验（PATCH 语义：不碰即不变）。
        """
        user = getattr(self.request, "user", None) if self.request is not None else None
        if user is None:
            # 无请求上下文（导入/脚本/内部调用）：范围语义不存在，不引入新约束
            return
        if "dept" in attrs:
            dept = attrs.get("dept")
            if dept is None:
                original = getattr(self.instance, "dept", None) if self.instance else None
                if original is not None and not getattr(user, "is_superuser", False):
                    raise ValidationError(_("The department is outside your data scope"))
            else:
                assert_within_data_scope(
                    DeptInfo.objects.filter(pk=dept.pk), user, _("The department is outside your data scope")
                )
        roles = attrs.get("roles")
        if roles:
            pks = [getattr(item, "pk", item) for item in roles]
            assert_within_data_scope(
                UserRole.objects.filter(pk__in=pks), user, _("Role is outside your assignable scope")
            )
        rules = attrs.get("rules")
        if rules:
            pks = [getattr(item, "pk", item) for item in rules]
            assert_within_data_scope(
                DataPermission.objects.filter(pk__in=pks), user, _("Data permission is outside your assignable scope")
            )

    def validate(self, attrs):
        self._assert_scope_fields(attrs)
        if attrs.get("invite"):
            # 邀请模式：密码由被邀请人自行设置（服务端置不可用），提交中的密码一律忽略
            attrs.pop("password", None)
            return attrs
        password = attrs.get("password")
        if password:
            if self.request.method == "POST":
                # 注意：密码规则必须校验解密后的明文。前端提交的是
                # AESCipherV2(username) 加密串，若拿提交原文校验，收紧
                # 大小写/数字规则后密文无法稳定满足（hex/base64 形态随机），
                # 会导致合法密码被拒。
                if settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED:
                    # 密文模式：前端建号提交的是加密串，解密失败直接拒绝，
                    # 不再把密文/明文误当密码落库（导入/脚本等明文提交场景应关闭该开关）
                    try:
                        plain_password = AESCipherV2(attrs.get("username")).decrypt(password)
                    except Exception as e:
                        logger.warning(f"create user password decrypt failed:{e}. rejected")
                        raise ValidationError(PASSWORD_DECRYPT_FAILED_MESSAGE, code=PASSWORD_DECRYPT_FAILED_CODE) from e
                    if not plain_password:
                        # 解密结果为空 = 密文认证失败（密钥不符/数据被篡改），同解密异常口径拒绝
                        raise ValidationError(PASSWORD_DECRYPT_FAILED_MESSAGE, code=PASSWORD_DECRYPT_FAILED_CODE)
                else:
                    # 明文模式（导入/E2E 等场景）：解密失败视为提交值本身是明文，按明文落库
                    try:
                        plain_password = AESCipherV2(attrs.get("username")).decrypt(password)
                    except Exception as e:
                        plain_password = password
                        logger.warning(f"create user password decrypt failed:{e}. fallback to submitted plaintext")
                if not check_password_rules(plain_password):
                    raise ValidationError(_("Password does not match security rules"))
                if check_leak_password(plain_password):
                    raise ValidationError(_("Password has been leaked, please change to another one"))
                attrs["password"] = make_password(plain_password)
            else:
                raise ValidationError(_("Abnormal password field"))
        return attrs

    def create(self, validated_data):
        invite = validated_data.pop("invite", False)
        instance = super().create(validated_data)
        if invite:
            # 创建即邀请：密码置不可用（登录被拒），由被邀请人从邀请链接自行设置
            instance.set_unusable_password()
            instance.save(update_fields=["password"])
            return instance
        # 建号即留存首条密码历史：后续改密的「最近 N 次不可复用」覆盖初始密码
        if validated_data.get("password"):
            record_password_hash(instance, instance.password)
        return instance


class ResetPasswordSerializer(serializers.Serializer):
    # 密码下限不在字段上硬编码：统一由 check_password_rules 按长度/复杂度开关判定
    # （普通用户 SECURITY_PASSWORD_MIN_LENGTH、超管 SECURITY_ADMIN_USER_PASSWORD_MIN_LENGTH），
    # 与注册、本人改密等改密链路共用同一套密码策略与报错文案
    password = serializers.CharField(max_length=128, required=True, write_only=True, label=_("Password"))

    def update(self, instance, validated_data):
        ensure_local_password_changeable(instance)
        try:
            password = AESCipherV2(instance.username).decrypt(validated_data.get("password"))
        except Exception as e:
            # 解密失败视同不满足密码策略，与下方规则校验同文案拒绝（避免裸异常落成 500）
            raise serializers.ValidationError(_("Password does not match security rules")) from e
        if not check_password_rules(password, instance.is_superuser):
            raise serializers.ValidationError(_("Password does not match security rules"))
        if check_leak_password(password):
            raise serializers.ValidationError(_("Password has been leaked, please change to another one"))
        if check_history_password(instance, password):
            raise serializers.ValidationError(
                _("Password cannot reuse the recent %(count)s passwords")
                % {"count": settings.SECURITY_PASSWORD_HISTORY_COUNT}
            )

        instance.set_password(password)
        instance.modifier = self.context.get("request").user
        instance.save(update_fields=["password", "modifier"])
        record_password_hash(instance, instance.password)
        return instance
