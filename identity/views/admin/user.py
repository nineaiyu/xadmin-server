#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : user
# author : ly_13
# date : 6/16/2023
import json
from typing import Any

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError

from audit.services import OperationLog
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
from identity.models import Post, UserInfo, UserOAuthBinding
from identity.serializers.user import (
    PASSWORD_DECRYPT_FAILED_MESSAGE,
    ResetPasswordSerializer,
    UserSerializer,
    is_password_decrypt_failure,
    record_create_password_decrypt_failure,
)
from identity.utils import user_invite
from identity.utils.impersonation import is_impersonating, start_impersonation
from message.services import send_logout_msg
from mfa.cache import UserConfirmStateCache
from mfa.confirm import UserConfirmation
from mfa.const import ConfirmType
from mfa.services import clear_recovery_codes
from notifications.message import SiteMessageUtil
from settings.services import LoginBlockUtil
from system.services.modelset import ChangeRolePermissionAction, PermissionPreviewAction
from system.services.tags import TagChoiceFilter, TagFilterBackend, TagFilterMixin, TaggedPrefetchMixin

logger = get_logger(__name__)


#: 邀请权限点 path（与 loadjson/menumeta.json 同源）：创建即邀请需同时具备该权限
INVITE_PERMISSION_PATH = "api/system/user/1/invite"


class UserFilter(TagFilterMixin, BaseFilterSet):
    username = filters.CharFilter(field_name="username", lookup_expr="icontains")
    tag = TagChoiceFilter()
    nickname = filters.CharFilter(field_name="nickname", lookup_expr="icontains")
    phone = filters.CharFilter(field_name="phone", lookup_expr="icontains")
    # 联动：角色列表「用户数」可点击跳转到按角色筛选的用户列表（传角色 pk）
    role = filters.CharFilter(field_name="roles", lookup_expr="pk")
    # 按岗位（多对多）筛选：多选主键，命中所选任一岗位即返回（空岗位用户不误筛）
    posts = filters.ModelMultipleChoiceFilter(field_name="posts", queryset=Post.objects.all())

    class Meta:
        model = UserInfo
        # 顺序即搜索区展示顺序：标签是高频筛查条件，前置到第 2 位
        # （搜索区收起态只渲染前 3 个字段，排在末尾等于"没有这个筛选项"）
        fields = [
            "username",
            "tag",
            "nickname",
            "phone",
            "email",
            "is_active",
            "gender",
            "pk",
            "dept",
            "role",
            "posts",
        ]


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
    # 批量更新白名单：批量改状态 / 归属 / 角色 / 性别（逐项序列化器校验）
    batch_update_fields = ("is_active", "dept", "roles", "gender")

    ordering_fields = ["date_joined", "last_login", "created_time"]
    filterset_class = UserFilter
    # 通用标签：?tag=<标签名> 过滤（AND 语义，与数据权限叠加）+ 列表预取（TaggedPrefetchMixin）
    # API 查询能力试点：受控 lookup 透传（字段面 = UserFilter 已声明字段；字段可见性 fail-closed）
    controlled_lookup = True
    extra_filter_class = [TagFilterBackend, ControlledLookupFilterBackend]

    # export_as_zip = True  导出zip压缩包，密码是用户名

    def get_permissions(self) -> Any:
        """删除用户（单删/批量删）与模拟用户为敏感操作，需先通过密码二次确认"""
        permissions = super().get_permissions()
        if self.action in ("destroy", "batch_destroy", "impersonate"):
            permissions.append(UserConfirmation.require(ConfirmType.PASSWORD)())
        return permissions

    def perform_destroy(self, instance: Any) -> Any:
        # 抛 ValidationError（400 + 可读文案）而不是裸 Exception（会归一成 500，
        # 前端只能看到「服务器错误」，排查与提示都失真）
        if instance.is_superuser:
            raise ValidationError(_("The super administrator disallows deletion"))
        return instance.delete()

    def create(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """创建用户；`invite=true` 一步完成邀请开户（无需密码 + 邮件邀请链接）。

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
        try:
            return super().create(request, *args, **kwargs)
        except ValidationError as exc:
            if not is_password_decrypt_failure(exc):
                raise
            # 密文模式下建号密码解密失败：先落审计再以业务码应答（同注册/忘记密码拒绝口径）。
            # 异常应答会触发请求事务回滚（common_exception_handler set_rollback），
            # 审计必须在回滚前落库，否则记录随事务一并丢失。
            record_create_password_decrypt_failure(request, request.data.get("username"))
            return ApiResponse(code=1001, detail=PASSWORD_DECRYPT_FAILED_MESSAGE, status=400)

    def perform_create(self, serializer: Any) -> None:
        super().perform_create(serializer)
        if user_invite.invite_requested(self.request.data):
            user_invite.send_invite(serializer.instance, request=self.request)

    @ApprovalRequired()
    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"pks": build_array_type(build_basic_type(OpenApiTypes.STR) or {})},
                required=["pks"],
                description="主键列表",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-destroy")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def batch_destroy(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """批量删除{cls}"""
        self.queryset = self.queryset.filter(is_superuser=False)
        return super().batch_destroy(request, *args, **kwargs)

    @ApprovalRequired()
    def destroy(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """删除{cls}数据"""
        return super().destroy(request, *args, **kwargs)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="reset-password", serializer_class=ResetPasswordSerializer)  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def reset_password(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """重置用户密码"""
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        SiteMessageUtil.notify_success(
            users=instance,
            title=_("Password reset successful"),
            message=_("Your password has been reset by an administrator"),
        )
        return ApiResponse()

    @extend_schema(responses=get_default_response_schema(), request=None)
    @action(methods=["post"], detail=True)  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def invite(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """发送/重发邀请激活邮件（重置为待激活 + 一次性链接；权限点 invite:SystemUser）"""
        instance = self.get_object()
        if not user_invite.mail_channel_configured():
            return ApiResponse(code=1001, detail=user_invite.INVITE_MAIL_UNAVAILABLE_MESSAGE)
        user_invite.send_invite(instance, request=request)
        return ApiResponse(detail=_("Invitation sent"))

    @extend_schema(responses=get_default_response_schema(), request=None)
    @action(methods=["post"], detail=True)  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def unblock(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """解禁用户"""
        instance = self.get_object()
        LoginBlockUtil.unblock_user(instance.username)
        return ApiResponse()

    @extend_schema(responses=get_default_response_schema(), request=None)
    @action(  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
        methods=["post"],
        detail=True,
        url_path="reset-mfa",
        permission_classes=[IsAuthenticated, UserConfirmation.require(ConfirmType.PASSWORD)],
    )
    def reset_mfa(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """重置{cls}MFA（清除 OTP 绑定，敏感操作：需密码二次确认）"""
        instance = self.get_object()
        instance.otp_secret_key = ""
        instance.mfa_level = UserInfo.MFALevelChoices.DISABLED
        instance.save(update_fields=["otp_secret_key", "mfa_level"])
        clear_recovery_codes(instance)
        UserConfirmStateCache(instance).clear()
        LoginBlockUtil.unblock_user(instance.username)
        return ApiResponse(detail=_("The user's MFA has been reset"))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"channel_names": build_array_type(build_basic_type(OpenApiTypes.STR) or {})},
                required=["channel_names"],
                description="列表",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True)  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def logout(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """强退用户"""
        instance = self.get_object()
        channel_names = request.data.get("channel_names", [])
        send_logout_msg(instance.pk, channel_names)
        return ApiResponse()

    @extend_schema(responses=get_default_response_schema(), request=None)
    @action(methods=["post"], detail=True)  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def impersonate(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """模拟用户（签发被模拟用户的 token，以其身份使用后台；impersonate 权限点 + 密码二次确认）"""
        if is_impersonating(request):
            return ApiResponse(code=1001, detail=_("You are already impersonating another user, exit first"))
        target = self.get_object()
        if target.pk == request.user.pk:
            return ApiResponse(code=1001, detail=_("Impersonating yourself is not allowed"))
        if target.is_superuser:
            return ApiResponse(code=1001, detail=_("Impersonating a super administrator is not allowed"))
        if not target.is_active:
            return ApiResponse(code=1001, detail=_("Disabled users cannot be impersonated"))
        data = start_impersonation(request, target, request.user)
        return ApiResponse(data=data, detail=_("Now impersonating user {}").format(target.username))

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
    @action(methods=["get", "post"], detail=True, url_path="im-binding")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def im_binding(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """管理员代录 IM 身份（免扫码）：GET 查看绑定，POST 创建或更新。

        与自助扫码绑定（identity/views/auth/oauth.py）共用 UserOAuthBinding；
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
    @action(methods=["post"], detail=True, url_path="im-unbind")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def im_unbind(self, request: Any, *args: Any, **kwargs: Any) -> Any:
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
