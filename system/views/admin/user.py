#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : user
# author : ly_13
# date : 6/16/2023

import json

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied

from common.core.approval import ApprovalRequired
from common.core.filter import BaseFilterSet, ControlledLookupFilterBackend
from common.core.modelset import (
    BaseModelSet,
    BatchPartialUpdateAction,
    ImportExportDataAction,
    RecycleBinAction,
    UploadFileAction,
)
from common.core.permission import IsAuthenticated, user_has_permission
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from message.services import send_logout_msg
from mfa.cache import UserConfirmStateCache
from mfa.confirm import UserConfirmation
from mfa.const import ConfirmType
from notifications.message import SiteMessageUtil
from settings.services import LoginBlockUtil
from system.models import OperationLog, UserInfo, UserOAuthBinding
from system.serializers.user import ResetPasswordSerializer, UserSerializer
from system.utils import user_invite
from system.utils.modelset import ChangeRolePermissionAction, PermissionPreviewAction
from system.utils.tags import TagChoiceFilter, TagFilterBackend, TagFilterMixin, TaggedPrefetchMixin

logger = get_logger(__name__)


#: 邀请权限点 path（与 loadjson/menumeta.json 同源）：创建即邀请（F-11）需同时具备该权限
INVITE_PERMISSION_PATH = "api/system/user/1/invite"


class UserFilter(TagFilterMixin, BaseFilterSet):
    username = filters.CharFilter(field_name="username", lookup_expr="icontains")
    tag = TagChoiceFilter()
    nickname = filters.CharFilter(field_name="nickname", lookup_expr="icontains")
    phone = filters.CharFilter(field_name="phone", lookup_expr="icontains")
    # F-12 联动：角色列表「用户数」可点击跳转到按角色筛选的用户列表（传角色 pk）
    role = filters.CharFilter(field_name="roles", lookup_expr="pk")

    class Meta:
        model = UserInfo
        fields = ["username", "nickname", "phone", "email", "is_active", "gender", "pk", "dept", "role", "tag"]


class UserViewSet(
    TaggedPrefetchMixin,
    BatchPartialUpdateAction,
    RecycleBinAction,
    BaseModelSet,
    UploadFileAction,
    ChangeRolePermissionAction,
    PermissionPreviewAction,
    ImportExportDataAction,
):
    """用户"""

    FILE_UPLOAD_FIELD = "avatar"
    queryset = UserInfo.objects.all()
    serializer_class = UserSerializer
    # F-1 批量更新白名单：批量改状态 / 归属 / 角色 / 性别（逐项序列化器校验）
    batch_update_fields = ("is_active", "dept", "roles", "gender")

    ordering_fields = ["date_joined", "last_login", "created_time"]
    filterset_class = UserFilter
    # P-1 通用标签：?tag=<标签名> 过滤（AND 语义，与数据权限叠加）+ 列表预取（TaggedPrefetchMixin）
    # F-13 API 查询能力试点：受控 lookup 透传（字段面 = UserFilter 已声明字段；字段可见性 fail-closed）
    controlled_lookup = True
    extra_filter_class = [TagFilterBackend, ControlledLookupFilterBackend]

    # export_as_zip = True  导出zip压缩包，密码是用户名

    def get_permissions(self):
        """删除用户（单删/批量删）为敏感操作，需先通过密码二次确认"""
        permissions = super().get_permissions()
        if self.action in ("destroy", "batch_destroy"):
            permissions.append(UserConfirmation.require(ConfirmType.PASSWORD)())
        return permissions

    def perform_destroy(self, instance):
        if instance.is_superuser:
            raise Exception(_("The super administrator disallows deletion"))
        return instance.delete()

    def create(self, request, *args, **kwargs):
        """创建用户；`invite=true` 一步完成邀请开户（F-11：无需密码 + 邮件邀请链接）。

        前置校验（fail-closed，不满足则不创建，避免产生「收不到邀请又无法登录」的死号）：
        邮件渠道已配置 + 具备邀请权限点 `invite:SystemUser`。
        """
        if user_invite.invite_requested(request.data):
            if not user_invite.mail_channel_configured():
                return ApiResponse(code=1001, detail=user_invite.INVITE_MAIL_UNAVAILABLE_MESSAGE)
            if not user_has_permission(request.user, INVITE_PERMISSION_PATH, "POST"):
                raise PermissionDenied(
                    str(_("You do not have permission to perform the action: {}").format(str(_("Invite activation"))))
                )
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        super().perform_create(serializer)
        if user_invite.invite_requested(self.request.data):
            user_invite.send_invite(serializer.instance, request=self.request)

    @ApprovalRequired()
    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"pks": build_array_type(build_basic_type(OpenApiTypes.STR))},
                required=["pks"],
                description="主键列表",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-destroy")
    def batch_destroy(self, request, *args, **kwargs):
        """批量删除{cls}"""
        self.queryset = self.queryset.filter(is_superuser=False)
        return super().batch_destroy(request, *args, **kwargs)

    @ApprovalRequired()
    def destroy(self, request, *args, **kwargs):
        """删除{cls}数据"""
        return super().destroy(request, *args, **kwargs)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="reset-password", serializer_class=ResetPasswordSerializer)
    def reset_password(self, request, *args, **kwargs):
        """重置用户密码"""
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        SiteMessageUtil.notify_error(users=instance, title="密码重置成功", message="密码被管理员重置成功")
        return ApiResponse()

    @extend_schema(responses=get_default_response_schema(), request=None)
    @action(methods=["post"], detail=True)
    def invite(self, request, *args, **kwargs):
        """发送/重发邀请激活邮件（F-11：重置为待激活 + 一次性链接；权限点 invite:SystemUser）"""
        instance = self.get_object()
        if not user_invite.mail_channel_configured():
            return ApiResponse(code=1001, detail=user_invite.INVITE_MAIL_UNAVAILABLE_MESSAGE)
        user_invite.send_invite(instance, request=request)
        return ApiResponse(detail=_("Invitation sent"))

    @extend_schema(responses=get_default_response_schema(), request=None)
    @action(methods=["post"], detail=True)
    def unblock(self, request, *args, **kwargs):
        """解禁用户"""
        instance = self.get_object()
        LoginBlockUtil.unblock_user(instance.username)
        return ApiResponse()

    @extend_schema(responses=get_default_response_schema(), request=None)
    @action(
        methods=["post"],
        detail=True,
        url_path="reset-mfa",
        permission_classes=[IsAuthenticated, UserConfirmation.require(ConfirmType.PASSWORD)],
    )
    def reset_mfa(self, request, *args, **kwargs):
        """重置{cls}MFA（清除 OTP 绑定，敏感操作：需密码二次确认）"""
        instance = self.get_object()
        instance.otp_secret_key = ""
        instance.mfa_level = UserInfo.MFALevelChoices.DISABLED
        instance.save(update_fields=["otp_secret_key", "mfa_level"])
        UserConfirmStateCache(instance).clear()
        LoginBlockUtil.unblock_user(instance.username)
        return ApiResponse(detail=_("The user's MFA has been reset"))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"channel_names": build_array_type(build_basic_type(OpenApiTypes.STR))},
                required=["channel_names"],
                description="列表",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True)
    def logout(self, request, *args, **kwargs):
        """强退用户"""
        instance = self.get_object()
        channel_names = request.data.get("channel_names", [])
        send_logout_msg(instance.pk, channel_names)
        return ApiResponse()

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "provider": build_basic_type(OpenApiTypes.STR),
                    "subject": build_basic_type(OpenApiTypes.STR),
                    "nickname": build_basic_type(OpenApiTypes.STR),
                },
                required=["provider", "subject"],
                description="IM provider（dingtalk/wecom/feishu 等）+ IdP 侧唯一标识（钉钉为 unionId）",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["get", "post"], detail=True, url_path="im-binding")
    def im_binding(self, request, *args, **kwargs):
        """管理员代录 IM 身份（免扫码）：GET 查看绑定，POST 创建或更新。

        与自助扫码绑定（system/views/auth/oauth.py）共用 UserOAuthBinding；
        钉钉的 subject 必须是 unionId（发消息前再换算 userid，见 notifications/backends/dingtalk.py）。
        写操作落 OperationLog（module=IM:binding）。
        """
        user = self.get_object()
        if request.method.upper() == "GET":
            rows = list(user.oauth_bindings.values("pk", "provider", "subject", "profile", "created_time"))
            return ApiResponse(data=rows)

        provider = (request.data.get("provider") or "").strip()
        subject = (request.data.get("subject") or "").strip()
        if not provider or not subject:
            return ApiResponse(code=1001, detail=_("Provider and subject are required"))
        conflict = UserOAuthBinding.objects.filter(provider=provider, subject=subject).exclude(user=user).first()
        if conflict:
            return ApiResponse(code=1001, detail=_("This identity is already bound to another user"))
        nickname = (request.data.get("nickname") or "").strip()
        binding, created = UserOAuthBinding.objects.update_or_create(
            user=user,
            provider=provider,
            defaults={"subject": subject, "profile": {"nickname": nickname, "source": "admin_manual"}},
        )
        OperationLog.objects.create(
            module="IM:binding",
            object_pk=str(user.pk),
            path=request.path,
            changes=json.dumps({"provider": provider, "subject": subject, "created": created}, ensure_ascii=False)[
                :4096
            ],
        )
        return ApiResponse(
            data={"pk": str(binding.pk), "created": created},
            detail=_("IM identity bound") if created else _("IM identity updated"),
        )

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"provider": build_basic_type(OpenApiTypes.STR)},
                required=["provider"],
                description="要解绑的 IM provider",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, url_path="im-unbind")
    def im_unbind(self, request, *args, **kwargs):
        """管理员解绑 IM 身份（防自锁：解绑后无其它登录方式则拒绝）。"""
        user = self.get_object()
        provider = (request.data.get("provider") or "").strip()
        binding = user.oauth_bindings.filter(provider=provider).first()
        if binding is None:
            return ApiResponse(code=1001, detail=_("Binding not found"))
        if not UserOAuthBinding.user_has_other_login_method(user, exclude_pk=binding.pk):
            return ApiResponse(code=1001, detail=_("User would lose the last login method"))
        binding.delete()
        OperationLog.objects.create(
            module="IM:binding",
            object_pk=str(user.pk),
            path=request.path,
            changes=json.dumps({"provider": provider, "action": "unbind"}, ensure_ascii=False)[:4096],
        )
        return ApiResponse(detail=_("IM identity unbound"))
