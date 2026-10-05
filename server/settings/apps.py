#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""应用与中间件装配（自 server/settings/base.py 平移；本体见本模块）。

base.py 经 build_installed_apps / build_middleware 调用后再导出同名 settings，
消费面（django 启动 / tests/settings_*.py 的 star-import）保持不变。
"""

from ..const import CONFIG


def build_installed_apps(xadmin_apps: list) -> list:
    """INSTALLED_APPS 装配。

    内置 app → 业务 app（拆分后各自独立）→ 三方库 → 配置追加的 xadmin_apps →
    common（必须最后，django ready）；随后两条条件装配：

    - django.contrib.postgres 无条件注册：PostgreSQL 专有索引（GinIndex / pg_trgm）
      静态存在于模型 Meta，该 app 必须参与模型检查（postgres.E005），否则
      ``check --database`` 失败会卡死服务启动（2026-09-18 部署事故根因）。
      非 PG 后端下 app 仅注册检查与 lookups、不产生任何 DDL（建索引迁移 0010 有
      vendor 守卫）；若条件化，显式使用 mysql/sqlite3 的部署会踩同样的 E005。
    - daphne 仅在 DEBUG/DEBUG_DEV 下插入（websocket 支持）。
    """
    apps = [
        "django.contrib.admin",
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
        "django.contrib.messages",
        "django.contrib.staticfiles",
        "identity.apps.IdentityConfig",  # 身份与认证域（Phase C 拆分批次1 自 system 迁出）
        "file.apps.FileConfig",  # 文件域（Phase C 拆分批次2 自 system 迁出）
        "audit.apps.AuditConfig",  # 审计域（Phase C 拆分批次3 自 system 迁出）
        "task.apps.TaskConfig",  # 任务域（Phase C 拆分批次4 自 system 迁出）
        "system.apps.SystemConfig",  # 系统管理（platform 面）
        "approval.apps.ApprovalConfig",  # 审批流（3.1 拆分批次2 自 system 迁出）
        "ai.apps.AiConfig",  # AI 平台与知识库（3.1 拆分批次3 自 system 迁出）
        "dataset.apps.DatasetConfig",  # 数据分析与动态表单（3.1 拆分批次4 自 system 迁出）
        "settings.apps.SettingsConfig",  # 设置相关
        "mfa.apps.MfaConfig",  # MFA / 敏感操作二次验证
        "notifications.apps.NotificationsConfig",  # 消息通知相关
        "captcha.apps.CaptchaConfig",  # 图片验证码
        "message.apps.MessageConfig",  # websocket 消息
        "integrations.apps.IntegrationsConfig",  # 外部服务接入（IM/短信/AI 适配器，自 common/sdk 迁出）
        "rest_framework_simplejwt",
        "rest_framework_simplejwt.token_blacklist",
        "corsheaders",
        "rest_framework",
        "django_filters",
        "django_celery_results",
        "django_celery_beat",
        "imagekit",
        "drf_spectacular",
        "drf_spectacular_sidecar",
        *xadmin_apps,
        "common.apps.CommonConfig",  # 这个放到最后, django ready
    ]
    # 演示种子命令（seed_demo_* 家族）条件注册：生产环境（非 DEBUG 且未启用 demo
    # app）不注册，manage.py 不再暴露演示数据入口；公开演示部署（XADMIN_APPS 含
    # demo，可 DEBUG=false）、开发与测试环境注册。命令名与行为与迁出前一致。
    if "demo" in xadmin_apps or CONFIG.DEBUG or CONFIG.DEBUG_DEV:
        apps.insert(apps.index("common.apps.CommonConfig"), "demo_seed.apps.DemoSeedConfig")
    apps.append("django.contrib.postgres")
    if CONFIG.DEBUG or CONFIG.DEBUG_DEV:
        apps.insert(0, "daphne")  # 支持websocket
    return apps


def build_middleware() -> list:
    """MIDDLEWARE 装配（顺序即语义，注释随行）。"""
    return [
        "server.middleware.StartMiddleware",
        "server.middleware.RequestMiddleware",
        # 功能模块裁剪：停用模块的请求直接 404（无停用模块时零开销）
        "server.middleware.ModuleGateMiddleware",
        "django.middleware.security.SecurityMiddleware",
        "django.contrib.sessions.middleware.SessionMiddleware",
        "corsheaders.middleware.CorsMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.middleware.locale.LocaleMiddleware",
        # /admin/ 站点已启用且依赖 Session+CSRF，必须恢复该中间件；
        # DRF API 视图自带 csrf_exempt，Bearer 接口不受影响
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django.contrib.messages.middleware.MessageMiddleware",
        "django.middleware.clickjacking.XFrameOptionsMiddleware",
        "server.middleware.RefererCheckMiddleware",
        "server.middleware.SQLCountMiddleware",
        # CSP（S3）：CSPModeMiddleware 必须排在 csp 中间件之前——响应阶段自内向外执行，
        # 它需要在 django-csp 生成策略头之后按系统配置改写/移除（disabled/report-only/enforce）
        "common.core.middleware.CSPModeMiddleware",
        "csp.middleware.CSPMiddleware",
        "common.core.middleware.ApiLoggingMiddleware",
        "server.middleware.EndMiddleware",
    ]
