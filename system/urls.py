#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : urls
# author : ly_13
# date : 6/6/2023
from django.urls import include, path, re_path
from rest_framework.routers import SimpleRouter

from common.core.routers import NoDetailRouter
from system.views.admin.account_risk import AccountRiskViewSet
from system.views.admin.approval import ApprovalRequestViewSet
from system.views.admin.approval_delegation import ApprovalDelegationViewSet
from system.views.admin.approval_flow import (
    ApprovalFlowViewSet,
    ApprovalInstanceViewSet,
)
from system.views.admin.approval_rule import ApprovalRuleViewSet
from system.views.admin.config import SystemConfigViewSet, UserPersonalConfigViewSet
from system.views.admin.credential import CredentialViewSet
from system.views.admin.dept import DeptViewSet
from system.views.admin.dict import DataDictViewSet
from system.views.admin.export import ExportRecordViewSet
from system.views.admin.file import UploadFileViewSet
from system.views.admin.import_ import ImportRecordViewSet, ImportTemplateViewSet
from system.views.admin.leave import LeaveViewSet
from system.views.admin.login_policy import LoginAccessPolicyViewSet
from system.views.admin.loginlog import LoginLogViewSet
from system.views.admin.mask import DataMaskRuleViewSet
from system.views.admin.menu import MenuViewSet
from system.views.admin.modelfield import ModelLabelFieldViewSet
from system.views.admin.online import UserOnlineViewSet
from system.views.admin.operationlog import OperationLogViewSet
from system.views.admin.passkey import PasskeyViewSet
from system.views.admin.permission import DataPermissionViewSet
from system.views.admin.role import RoleViewSet
from system.views.admin.saved_view import SavedListViewSet
from system.views.admin.user import UserViewSet
from system.views.ai import (
    AiAssistantSettingViewSet,
    AiAssistantViewSet,
    AiKnowledgeDocumentViewSet,
    AiProfileViewSet,
)
from system.views.ai.mcp import McpEndpointAPIView
from system.views.analysis import ReportViewSet, ScreenViewSet
from system.views.auth.invite import InviteAcceptAPIView, InviteValidateAPIView
from system.views.auth.login import BasicLoginAPIView, VerifyCodeLoginAPIView
from system.views.auth.logout import LogoutAPIView
from system.views.auth.mfa import (
    LoginMFAPasskeyChallengeAPIView,
    LoginMFASendCodeAPIView,
    LoginMFAVerifyAPIView,
)
from system.views.auth.oauth import (
    OAuthAuthorizeAPIView,
    OAuthBindAuthorizeAPIView,
    OAuthBindingsAPIView,
    OAuthCallbackAPIView,
    OAuthProvidersAPIView,
    OAuthUnbindAPIView,
)
from system.views.auth.register import RegisterViewAPIView
from system.views.auth.reset import ResetPasswordAPIView
from system.views.auth.rule import PasswordRulesAPIView
from system.views.auth.token import CaptchaAPIView, RefreshTokenAPIView, TempTokenAPIView
from system.views.auth.verify_code import SendVerifyCodeAPIView
from system.views.configs import ConfigsViewSet
from system.views.dashboard import DashboardViewSet
from system.views.dataset import DashboardViewSet as DataDashboardViewSet
from system.views.dataset import DatasetViewSet
from system.views.dform import DynamicFormSubmissionViewSet, DynamicFormViewSet
from system.views.modules import SystemModuleViewSet
from system.views.monitor import MonitorViewSet
from system.views.open import ApiApplicationTokenAPIView, ApiApplicationViewSet
from system.views.open_oauth import (
    OpenOAuthApproveAPIView,
    OpenOAuthAuthorizeAPIView,
    OpenOAuthRevokeAPIView,
    OpenOAuthTokenAPIView,
)
from system.views.routes import UserRoutesAPIView
from system.views.search.dept import SearchDeptViewSet
from system.views.search.global_search import GlobalSearchAPIView
from system.views.search.menu import SearchMenuViewSet
from system.views.search.role import SearchRoleViewSet
from system.views.search.user import SearchUserViewSet
from system.views.tag import TagViewSet
from system.views.task import (
    CrontabScheduleViewSet,
    IntervalScheduleViewSet,
    PeriodicTaskViewSet,
    TaskExecutionViewSet,
)
from system.views.task_center import SystemTaskCenterViewSet
from system.views.user.login_log import UserLoginLogViewSet
from system.views.user.token import PersonalAccessTokenViewSet
from system.views.user.userinfo import UserInfoViewSet
from system.views.webhook import WebhookDeliveryViewSet, WebhookSubscriptionViewSet

app_name = "system"

router = SimpleRouter(False)
no_detail_router = NoDetailRouter(False)

no_auth_url = [
    re_path("^captcha/", include("captcha.urls")),
    re_path("^login/basic$", BasicLoginAPIView.as_view(), name="login-by-basic"),
    re_path("^login/code$", VerifyCodeLoginAPIView.as_view(), name="login-by-code"),
    re_path("^login/mfa/send-code$", LoginMFASendCodeAPIView.as_view(), name="login-mfa-send-code"),
    re_path("^login/mfa/verify$", LoginMFAVerifyAPIView.as_view(), name="login-mfa-verify"),
    re_path(
        "^login/mfa/passkey/challenge$",
        LoginMFAPasskeyChallengeAPIView.as_view(),
        name="login-mfa-passkey-challenge",
    ),
    re_path("^register$", RegisterViewAPIView.as_view(), name="register"),
    re_path("^auth/captcha$", CaptchaAPIView.as_view(), name="captcha"),
    re_path("^auth/token$", TempTokenAPIView.as_view(), name="temp_token"),
    re_path("^auth/verify$", SendVerifyCodeAPIView.as_view(), name="send-verify-code"),
    re_path("^auth/reset$", ResetPasswordAPIView.as_view(), name="reset-password"),
    # 邀请激活：令牌即凭据，激活页未登录，必须匿名可达
    re_path("^auth/invite/validate$", InviteValidateAPIView.as_view(), name="invite-validate"),
    re_path("^auth/invite/accept$", InviteAcceptAPIView.as_view(), name="invite-accept"),
    # 第三方登录：authorize/callback 必须匿名可达，故挂在 no_auth_url
    re_path("^auth/oauth/providers$", OAuthProvidersAPIView.as_view(), name="oauth-providers"),
    re_path(
        "^auth/oauth/(?P<provider>[^/]+)/authorize$",
        OAuthAuthorizeAPIView.as_view(),
        name="oauth-authorize",
    ),
    re_path(
        "^auth/oauth/(?P<provider>[^/]+)/callback$",
        OAuthCallbackAPIView.as_view(),
        name="oauth-callback",
    ),
    # 绑定意图的授权地址（同样是白名单路径，视图内要求 DRF IsAuthenticated）
    re_path(
        "^auth/oauth/(?P<provider>[^/]+)/bind-authorize$",
        OAuthBindAuthorizeAPIView.as_view(),
        name="oauth-bind-authorize",
    ),
    re_path("^auth/oauth/bindings$", OAuthBindingsAPIView.as_view(), name="oauth-bindings"),
    re_path(
        "^auth/oauth/bindings/(?P<pk>[^/]+)$",
        OAuthUnbindAPIView.as_view(),
        name="oauth-unbind",
    ),
]

auth_url = [
    re_path("^logout$", LogoutAPIView.as_view(), name="logout"),
    re_path("^refresh$", RefreshTokenAPIView.as_view(), name="refresh"),
    re_path("^rules/password$", PasswordRulesAPIView.as_view(), name="password-rules"),
]

router_url = [
    re_path("^routes$", UserRoutesAPIView.as_view(), name="user_routes"),
]
# 面板信息
router.register("dashboard", DashboardViewSet, basename="dashboard")
router.register("monitor", MonitorViewSet, basename="monitor")

# 仅数据搜索
router.register("search/user", SearchUserViewSet, basename="SearchUser")
router.register("search/role", SearchRoleViewSet, basename="SearchRole")
router.register("search/dept", SearchDeptViewSet, basename="SearchDept")
router.register("search/menu", SearchMenuViewSet, basename="SearchMenu")

# 个人用户信息
no_detail_router.register("userinfo", UserInfoViewSet, basename="userinfo")
router.register("user/log", UserLoginLogViewSet, basename="user_login_log")
router.register("configs", ConfigsViewSet, basename="configs")
router.register("personal-access-tokens", PersonalAccessTokenViewSet, basename="personal_access_token")

# 系统设置相关路由
router.register("user", UserViewSet, basename="user")
router.register("approvals", ApprovalRequestViewSet, basename="approval_request")
# 审批规则：按请求路径配置多级审批链（指定人/角色，逐级通知与推进）
router.register("approval-rules", ApprovalRuleViewSet, basename="approval_rule")
# 全量审批流引擎：流程定义 + 流程实例（流程审批中心）+ 审批委托（三期）
router.register("approval-flows", ApprovalFlowViewSet, basename="approval_flow")
router.register("approval-instances", ApprovalInstanceViewSet, basename="approval_instance")
router.register("approval-delegations", ApprovalDelegationViewSet, basename="approval_delegation")
router.register("dept", DeptViewSet, basename="dept")
router.register("menu", MenuViewSet, basename="menu")
router.register("role", RoleViewSet, basename="role")
router.register("permission", DataPermissionViewSet, basename="permission")
router.register("field", ModelLabelFieldViewSet, basename="model_label_field")
router.register("dict", DataDictViewSet, basename="data_dict")
router.register("mask-rules", DataMaskRuleViewSet, basename="data_mask_rule")
router.register("online", UserOnlineViewSet, basename="online_socket")
# 安全域：账号风险巡检 / 登录访问策略 / Passkey 凭据
router.register("account-risks", AccountRiskViewSet, basename="account_risk")
router.register("login-policies", LoginAccessPolicyViewSet, basename="login_policy")
router.register("passkeys", PasskeyViewSet, basename="passkey")
# 列表「我的视图」
router.register("saved-views", SavedListViewSet, basename="saved_view")

# 配置相关
router.register("config/system", SystemConfigViewSet, basename="sysconfig")
# 凭据与密钥：只读聚合 + 重加密轮换
router.register("credentials", CredentialViewSet, basename="credential")
# 功能模块清单（只读）：模块等级/依赖/启停状态与裁剪配置片段
router.register("modules", SystemModuleViewSet, basename="module")
# 数据集与仪表盘（可视化一期）
router.register("datasets", DatasetViewSet, basename="dataset")
router.register("dashboards", DataDashboardViewSet, basename="dashboards")
# 大屏与定时报表
router.register("screens", ScreenViewSet, basename="screen")
router.register("reports", ReportViewSet, basename="report")
# 出站 Webhook
router.register("webhooks/subscriptions", WebhookSubscriptionViewSet, basename="webhook-subscription")
router.register("webhooks/deliveries", WebhookDeliveryViewSet, basename="webhook-delivery")
# 动态表单
router.register("dynamic-forms", DynamicFormViewSet, basename="dynamic-form")
router.register("dynamic-form-submissions", DynamicFormSubmissionViewSet, basename="dynamic-form-submission")
# 请假申请：审批流引擎的第一个真实业务接入方
router.register("leaves", LeaveViewSet, basename="leave")
# AI 助手：配置（Setting 体系）与问答
no_detail_router.register("ai/assistant/config", AiAssistantSettingViewSet, basename="ai-assistant-config")
no_detail_router.register("ai/assistant", AiAssistantViewSet, basename="ai-assistant")
# AI 知识库文档管理：上传/预览/启停/删除 + 仓库文档重建
router.register("ai/knowledge-documents", AiKnowledgeDocumentViewSet, basename="ai-knowledge-document")
# AI 配置档案：多套凭据/采样参数，激活唯一（无激活档案回落 Setting 通路）
router.register("ai/profiles", AiProfileViewSet, basename="ai-profile")
router.register("config/user", UserPersonalConfigViewSet, basename="userconfig")

# 日志相关
router.register("logs/operation", OperationLogViewSet, basename="operation_log")
router.register("logs/login", LoginLogViewSet, basename="login_log")

# 文件管理
router.register("file", UploadFileViewSet, basename="file")

# 导出下载中心
router.register("exports", ExportRecordViewSet, basename="export_record")
# 导入记录（下载中心「导入记录」页签）
router.register("imports", ImportRecordViewSet, basename="import_record")
# 导入列映射模板（个人 / 全局共享，导入弹窗内维护，无独立页面）
router.register("import-templates", ImportTemplateViewSet, basename="import_template")

# 开放平台应用：client-credentials 应用管理与回调测试
router.register("api-applications", ApiApplicationViewSet, basename="api_application")

# 定时任务管理（django_celery_beat）
router.register("tasks/periodic", PeriodicTaskViewSet, basename="periodic_task")
router.register("tasks/crontab", CrontabScheduleViewSet, basename="crontab_schedule")
router.register("tasks/executions", TaskExecutionViewSet, basename="task_execution")
router.register("tasks/interval", IntervalScheduleViewSet, basename="interval_schedule")
# 任务中心：三类记录统一列表 + 取消 / 重跑
router.register("tasks/unified", SystemTaskCenterViewSet, basename="task_center")
# 通用标签中心：标签 CRUD + 打标 / 批量打标
router.register("tags", TagViewSet, basename="tag")

urlpatterns = no_auth_url + auth_url + router_url + router.urls + no_detail_router.urls
# 全局搜索：独立 GET 接口，权限码 retrieve:SystemGlobalSearch（种子登记）
urlpatterns += [path("global-search", GlobalSearchAPIView.as_view())]
# MCP 协议端点（Streamable HTTP 无状态）：外部 MCP 客户端经 PAT 接入统一工具层
urlpatterns += [path("ai/mcp", McpEndpointAPIView.as_view())]
# 开放平台换发端点：匿名可达（白名单），凭 client_secret 换 PAT 凭证
urlpatterns += [path("open/token", ApiApplicationTokenAPIView.as_view())]
# 开放平台 OAuth 授权码：authorize/approve 需登录态，token/revoke 匿名可达
urlpatterns += [
    path("open/oauth/authorize", OpenOAuthAuthorizeAPIView.as_view()),
    path("open/oauth/approve", OpenOAuthApproveAPIView.as_view()),
    path("open/oauth/token", OpenOAuthTokenAPIView.as_view()),
    path("open/oauth/revoke", OpenOAuthRevokeAPIView.as_view()),
]
