#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : setting
# author : ly_13
# date : 10/18/2024
"""Django settings 的业务配置转发层（T03-06 表驱动）。

此前 200+ 行逐键手写 ``X = CONFIG.X``：新增 CONFIG 键漏转发时，读取方
``getattr(settings, X, 默认值)`` 永远落默认值、无任何报错（SECURITY_AES_V1_
DECRYPT_ENABLED 曾因漏转发无法关闭，METRICS_ENABLED 漏转发端点永远 404）。
现改为键清单集中登记 + 循环写入模块 globals，新增键只改 FORWARD_KEYS 清单。

守护测试（tests/unit/server/test_settings_forwarding.py）做双向校验：

1. CONFIG.defaults 全量键必须落在三处之一——FORWARD_KEYS（本清单）、
   NON_FORWARDED_KEYS（有意不转发的豁免登记）、或被 server/ 装配模块以
   ``CONFIG.<key>`` 直接消费；新键两头都不沾即 CI 失败；
2. FORWARD_KEYS 中的键必须真实存在于 CONFIG.defaults（防手误静默取 None）。

只把「django settings 消费面」的键登记进 FORWARD_KEYS：运行期经 Setting 表
热更新覆盖的键全在此清单；装配期键（DB_*/REDIS_*/CELERY_* 等，本包其他模块
装配 django settings 时直接读 CONFIG）与 common 注入面（T03-04 后经
common.injection.get_server_config 读取的 FILE_*/APPROVAL_* 等）不转发，
逐键登记在 NON_FORWARDED_KEYS 并注明消费方。
"""

import os

from ..const import CONFIG, PROJECT_DIR

# ---------------------------------------------------------------------------
# 声明式转发键清单：同名转发 CONFIG.<key> -> django settings.<key>
# （运行期 Setting 表热更新按同名覆盖，见 settings app 的 signal_handlers）
# ---------------------------------------------------------------------------
FORWARD_KEYS = [
    # 密码安全配置
    "SECURITY_PASSWORD_MIN_LENGTH",
    "SECURITY_ADMIN_USER_PASSWORD_MIN_LENGTH",
    "SECURITY_PASSWORD_UPPER_CASE",
    "SECURITY_PASSWORD_LOWER_CASE",
    "SECURITY_PASSWORD_NUMBER",
    "SECURITY_PASSWORD_SPECIAL_CHAR",
    # 泄露密码库校验开关（内置离线弱口令清单，默认关闭渐进启用）
    "SECURITY_PASSWORD_LEAK_CHECK_ENABLED",
    # 密码历史：最近 N 次不可复用（0 = 关闭）
    "SECURITY_PASSWORD_HISTORY_COUNT",
    # 密码有效期（天数，0 = 永不过期；date_password_updated 为空的存量用户 = 宽限期）
    "SECURITY_PASSWORD_EXPIRATION_DAYS",
    # AES 旧格式（Salted__）解密灰度开关：读取方 common/base/utils.py 走
    # getattr(settings, ...)——漏转发会让开关永远落默认值（形同虚设）
    "SECURITY_AES_V1_DECRYPT_ENABLED",
    # 用户登录限制的规则
    "SECURITY_LOGIN_LIMIT_COUNT",
    "SECURITY_LOGIN_LIMIT_TIME",  # Unit: minute
    "SECURITY_CHECK_DIFFERENT_CITY_LOGIN",
    # 新设备/新 IP 登录提醒（异常登录提醒第二维度）
    "SECURITY_ABNORMAL_LOGIN_ALERT_ENABLED",
    "SECURITY_LOGIN_BASELINE_DAYS",
    # 登录IP限制的规则
    "SECURITY_LOGIN_IP_BLACK_LIST",
    "SECURITY_LOGIN_IP_WHITE_LIST",
    "SECURITY_LOGIN_IP_LIMIT_COUNT",
    "SECURITY_LOGIN_IP_LIMIT_TIME",
    # 登陆规则
    "SECURITY_LOGIN_ACCESS_ENABLED",
    "SECURITY_LOGIN_CAPTCHA_ENABLED",
    "SECURITY_LOGIN_ENCRYPTED_ENABLED",
    "SECURITY_LOGIN_TEMP_TOKEN_ENABLED",
    "SECURITY_LOGIN_BY_EMAIL_ENABLED",
    "SECURITY_LOGIN_BY_SMS_ENABLED",
    "SECURITY_LOGIN_BY_BASIC_ENABLED",
    # 注册规则
    "SECURITY_REGISTER_ACCESS_ENABLED",
    "SECURITY_REGISTER_CAPTCHA_ENABLED",
    "SECURITY_REGISTER_ENCRYPTED_ENABLED",
    "SECURITY_REGISTER_TEMP_TOKEN_ENABLED",
    "SECURITY_REGISTER_BY_EMAIL_ENABLED",
    "SECURITY_REGISTER_BY_SMS_ENABLED",
    "SECURITY_REGISTER_BY_BASIC_ENABLED",
    # 忘记密码规则
    "SECURITY_RESET_PASSWORD_ACCESS_ENABLED",
    "SECURITY_RESET_PASSWORD_CAPTCHA_ENABLED",
    "SECURITY_RESET_PASSWORD_TEMP_TOKEN_ENABLED",
    "SECURITY_RESET_PASSWORD_ENCRYPTED_ENABLED",
    "SECURITY_RESET_PASSWORD_BY_EMAIL_ENABLED",
    "SECURITY_RESET_PASSWORD_BY_SMS_ENABLED",
    # 绑定邮箱
    "SECURITY_BIND_EMAIL_ACCESS_ENABLED",
    "SECURITY_BIND_EMAIL_CAPTCHA_ENABLED",
    "SECURITY_BIND_EMAIL_TEMP_TOKEN_ENABLED",
    "SECURITY_BIND_EMAIL_ENCRYPTED_ENABLED",
    # 绑定手机
    "SECURITY_BIND_PHONE_ACCESS_ENABLED",
    "SECURITY_BIND_PHONE_CAPTCHA_ENABLED",
    "SECURITY_BIND_PHONE_TEMP_TOKEN_ENABLED",
    "SECURITY_BIND_PHONE_ENCRYPTED_ENABLED",
    # 临时令牌（tmp_token）有效期（秒）：登录/注册/重置/绑定加密握手共用
    "SECURITY_TEMP_TOKEN_EXPIRE",
    # MFA / 敏感操作二次验证
    "SECURITY_MFA_CONFIRM_ENABLED",
    "SECURITY_MFA_CONFIRM_BACKENDS",
    "SECURITY_MFA_VERIFY_TTL",  # Unit: second
    "SECURITY_MFA_PASSWORD_CONFIRM_TTL",  # Unit: second
    "SECURITY_MFA_LOGIN_PROTECT_ENABLED",
    "SECURITY_MFA_LOGIN_TOKEN_TTL",  # Unit: second
    "SECURITY_MFA_OTP_VALID_WINDOW",
    "SECURITY_MFA_OTP_ISSUER",
    # 认证方式策略：全局允许方式白名单（空 = 全部后端）
    "SECURITY_MFA_METHODS",
    # 账号安全风险巡检
    "SECURITY_PASSWORD_STALE_DAYS",
    "SECURITY_ACCOUNT_IDLE_DAYS",
    "SECURITY_SUPERUSER_MAX_COUNT",
    # 登录访问策略：并发会话上限（0 = 不限）
    "SECURITY_LOGIN_MAX_SESSIONS",
    # 上传安全策略与文件访问日志保留期
    "SECURITY_UPLOAD_BLOCK_EXTENSIONS",
    "SECURITY_UPLOAD_ALLOW_EXTENSIONS",
    "FILE_ACCESS_LOG_KEEP_DAYS",
    # 资源告警阈值
    "SECURITY_MONITOR_DISK_USED_MAX",
    "SECURITY_MONITOR_MEMORY_USED_MAX",
    "SECURITY_MONITOR_CPU_PERCENT_MAX",
    "SECURITY_MONITOR_CPU_LOAD_MAX",
    # 基本配置
    "SITE_URL",
    "FRONT_END_WEB_WATERMARK_ENABLED",  # 前端水印展示
    "FRONT_END_WEB_WATERMARK_TEXT",  # 前端水印文案（留空 = 用户名-昵称-时间）
    "FRONT_END_WEB_WATERMARK_PATHS",  # 前端水印生效页面（逗号分隔路由前缀）
    "FRONT_END_WEB_WATERMARK_FONT_SIZE",  # 前端水印字号（像素）
    "FRONT_END_WEB_WATERMARK_OPACITY",  # 前端水印透明度（0.01-1）
    "FRONT_END_WEB_WATERMARK_ROTATE",  # 前端水印旋转角度（度）
    "FRONT_END_WEB_WATERMARK_COLOR",  # 前端水印文字颜色（十六进制/rgba，留空 = 默认灰）
    "PERMISSION_FIELD_ENABLED",  # 字段权限控制
    "PERMISSION_DATA_ENABLED",  # 数据权限控制
    "REFERER_CHECK_ENABLED",  # referer 校验
    "EXPORT_MAX_LIMIT",  # 限制导出数据数量
    # 软删除回收站保留天数（purge 周期任务按此物理清除）
    "RECYCLE_BIN_RETENTION_DAYS",
    # 字段级审计 diff 白名单（模型 _meta.label 列表，空=关闭）
    "AUDIT_DIFF_MODELS",
    # 验证码配置
    "VERIFY_CODE_TTL",  # Unit: second
    "VERIFY_CODE_LIMIT",
    "VERIFY_CODE_LENGTH",
    "VERIFY_CODE_LOWER_CASE",
    "VERIFY_CODE_UPPER_CASE",
    "VERIFY_CODE_DIGIT_CASE",
    # LDAP/AD 目录同步：默认值在 server/conf；运行期经 Setting 体系热更新覆盖
    "LDAP_AUTH_ENABLED",
    "LDAP_AUTH_PRIORITY",
    "LDAP_AUTH_AUTO_CREATE",
    "LDAP_SERVER_URI",
    "LDAP_START_TLS",
    "LDAP_BIND_DN",
    "LDAP_BIND_PASSWORD",
    "LDAP_CONNECT_TIMEOUT",
    "LDAP_USER_SEARCH_BASE",
    "LDAP_USER_FILTER",
    "LDAP_ATTR_USERNAME",
    "LDAP_ATTR_NICKNAME",
    "LDAP_ATTR_EMAIL",
    "LDAP_ATTR_PHONE",
    "LDAP_DEPT_ENABLED",
    "LDAP_DEPT_SEARCH_BASE",
    "LDAP_SYNC_ENABLED",
    "LDAP_SYNC_AUTO_CREATE",
    "LDAP_SYNC_MISSING_POLICY",
    "LDAP_ATTR_GROUPS",
    "LDAP_GROUP_ROLE_MAP",
    "LDAP_SYNC_PAGE_SIZE",
    # 企业 IM 通知渠道
    "DINGTALK_ENABLED",
    "DINGTALK_APP_KEY",
    "DINGTALK_APP_SECRET",
    "DINGTALK_AGENT_ID",
    "WECOM_ENABLED",
    "WECOM_CORP_ID",
    "WECOM_CORP_SECRET",
    "WECOM_AGENT_ID",
    "FEISHU_ENABLED",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    # 聊天：历史 ws/message 通道的直广播兼容开关（默认关闭，见 message/notify.py）
    "CHAT_LEGACY_WS_BROADCAST_ENABLED",
    # AI 助手
    "AI_ASSISTANT_ENABLED",
    "AI_BASE_URL",
    "AI_API_KEY",
    "AI_MODEL",
    "AI_TIMEOUT",
    "AI_NL_QUERY_ENABLED",
    # 灰度开关：不转发则 config.yml 里配置不生效（只能靠 Setting 表热更新注入）
    "AI_ACTION_ENABLED",
    # AI 采样/行为参数（多档案回落通路）
    "AI_TEMPERATURE",
    "AI_MAX_TOKENS",
    "AI_STRUCTURED_MAX_TOKENS",
    # 安全护栏（引用数据隔离 / 注入标记 / 输出脱敏，默认开）
    "AI_GUARD_ENABLED",
    "AI_OUTPUT_MASK_ENABLED",
    # 原生 function calling 双轨（默认关，能力探测通过后可开启）
    "AI_NATIVE_TOOLS_ENABLED",
    # 用量配额（0 = 不限）
    "AI_QUOTA_USER_DAILY_CALLS",
    "AI_QUOTA_USER_DAILY_TOKENS",
    "AI_QUOTA_MAX_CONCURRENT_STREAMS",
    "AI_TOP_P",
    "AI_FREQUENCY_PENALTY",
    "AI_PRESENCE_PENALTY",
    "AI_STOP",
    "AI_SEED",
    "AI_MAX_RETRIES",
    "AI_CONTEXT_LIMIT",
    "AI_PERSONA",
    # 邮件配置
    "EMAIL_ENABLED",
    "EMAIL_HOST",
    "EMAIL_PORT",
    "EMAIL_HOST_USER",
    "EMAIL_HOST_PASSWORD",
    "EMAIL_FROM",
    "EMAIL_RECIPIENT",
    "EMAIL_SUBJECT_PREFIX",
    "EMAIL_USE_SSL",
    "EMAIL_USE_TLS",
    # 短信配置
    "SMS_ENABLED",
    "SMS_BACKEND",
    "SMS_TEST_PHONE",
    # 短信通知模板（通知渠道用；未配置时渠道自动降级为不可用）
    "SMS_NOTIFY_SIGN_NAME",
    "SMS_NOTIFY_TEMPLATE_CODE",
    "SMS_NOTIFY_TEMPLATE_PARAM_KEY",
    # 阿里云短信配置
    "ALIBABA_ACCESS_KEY_ID",
    "ALIBABA_ACCESS_KEY_SECRET",
    "ALIBABA_VERIFY_SIGN_NAME",
    "ALIBABA_VERIFY_TEMPLATE_CODE",
    # 图片验证码
    "CAPTCHA_IMAGE_SIZE",  # 设置 captcha 图片大小
    "CAPTCHA_CHALLENGE_FUNCT",
    "CAPTCHA_LENGTH",  # 字符个数,仅针对随机字符串生效
    "CAPTCHA_TIMEOUT",  # 超时(minutes)
    "CAPTCHA_FONT_SIZE",
    "CAPTCHA_BACKGROUND_COLOR",
    "CAPTCHA_FOREGROUND_COLOR",
    "CAPTCHA_NOISE_FUNCTIONS",
]

for _key in FORWARD_KEYS:
    globals()[_key] = getattr(CONFIG, _key)

del _key

# 派生值（非 CONFIG 键）：密码规则开关聚合，成员必须都在 FORWARD_KEYS 中
SECURITY_PASSWORD_RULES = [
    "SECURITY_PASSWORD_MIN_LENGTH",
    "SECURITY_PASSWORD_UPPER_CASE",
    "SECURITY_PASSWORD_LOWER_CASE",
    "SECURITY_PASSWORD_NUMBER",
    "SECURITY_PASSWORD_SPECIAL_CHAR",
]

# 下面图片验证码 默认配置（captcha 库固定键，非 CONFIG 键）
CAPTCHA_OUTPUT_FORMAT = "%(image)s %(text_field)s %(hidden_field)s "
# CAPTCHA_NOISE_FUNCTIONS = ("captcha.helpers.noise_arcs", "captcha.helpers.noise_dots")
# CAPTCHA_CHALLENGE_FUNCT = 'captcha.helpers.random_char_challenge'
CAPTCHA_FONT_PATH = os.path.join(PROJECT_DIR, "captcha", "fonts", "Vera.ttf")
CAPTCHA_LETTER_ROTATION = (-35, 35)
CAPTCHA_FILTER_FUNCTIONS = ("captcha.helpers.post_smooth",)
CAPTCHA_PUNCTUATION = """_"',.;:-"""
CAPTCHA_FLITE_PATH = None
CAPTCHA_SOX_PATH = None
CAPTCHA_MATH_CHALLENGE_OPERATOR = "*"
CAPTCHA_GET_FROM_POOL = False
CAPTCHA_GET_FROM_POOL_TIMEOUT = 5
CAPTCHA_2X_IMAGE = True

# 纯读请求免 ATOMIC_REQUESTS（见 common/core/atomic_read.py）：GET/HEAD 且 action 命中
# DRF 读动作白名单的请求不套事务（省 BEGIN/COMMIT 两次数据库往返）；写请求与自定义
# GET action（导出/同步等带副作用）保持原语义。config.yml 置 false 可整体回退。
# 特殊转发（bool 归一），不进 FORWARD_KEYS——由守护测试按「CONFIG 直接引用」豁免。
ATOMIC_REQUESTS_SKIP_READ_ACTIONS = bool(CONFIG.ATOMIC_REQUESTS_SKIP_READ_ACTIONS)

# ---------------------------------------------------------------------------
# 有意不转发到 django settings 的 CONFIG 键豁免登记（key -> 消费方/原因）。
# 这些键由消费方直接读 CONFIG（装配期或经 common.injection.get_server_config
# 注入面，T03-04），不进 django settings；新键必须登记到这里或 FORWARD_KEYS。
# ---------------------------------------------------------------------------
NON_FORWARDED_KEYS = {
    # 启动装配条件（非 django settings 面）
    "AUTO_MIGRATE": "services 命令 hands.py 启动判断",
    "SECRET_KEY_AUTO_GENERATE": "base.py 密钥兜底判断（config.get 字符串形式）",
    # 密钥/令牌：common/core/credentials.py 与告警 API 直读
    "BACKUP_ALERT_TOKEN": "备份告警令牌，common/api/backup.py 直读",
    "OPS_ALERT_TOKEN": "资源告警令牌，common/api/ops_alert.py 直读",
    # common/core/config/base.py（注入面配置基座）
    "CSP_MODE": "CSP 策略模式，conf base/csp 装配消费",
    "CSP_REPORT_URI": "CSP 上报端点，common/api/csp.py 直读",
    "SCIM_TOKEN": "SCIM Bearer 令牌，conf base + credentials 直读",
    "SLOW_REQUEST_THRESHOLD": "慢请求阈值，conf base + conf_ops 消费",
    # common/core/config/conf_security.py（注入面：审批/敏感操作/SCIM/OAUTH/限流）
    "APPROVAL_APPROVER_PERMS": "审批注入面 conf_security",
    "APPROVAL_APPROVER_ROLES": "审批注入面 conf_security",
    "APPROVAL_FLOW_KEEP_DAYS": "审批注入面 conf_security",
    "APPROVAL_KEEP_DAYS": "审批注入面 conf_security",
    "APPROVAL_MFA_REQUIRED_ACTIONS": "审批注入面 conf_security",
    "APPROVAL_PENDING_TIMEOUT": "审批注入面 conf_security",
    "APPROVAL_REMIND_HOURS": "审批注入面 conf_security",
    "APPROVAL_REQUIRED_PATHS": "审批拦截路径，conf_security + approval.py 消费",
    "APPROVAL_TOKEN_TTL": "审批注入面 conf_security",
    "LEAVE_APPROVAL_FLOW_CODE": "请假审批流编码，conf_security 消费",
    "MANUAL_RUNNABLE_TASKS": "手动任务白名单，conf_security + task_whitelist",
    "OAUTH_PROVIDERS": "OAuth 提供商配置，conf_security + credentials",
    "OUTBOUND_ALLOWED_HOSTS": "出站白名单，conf_security + webhook 消费",
    "PAT_RATE_LIMIT": "PAT 限流，conf_security + throttle 消费",
    "SCIM_DEFAULT_ROLE_CODE": "SCIM 默认角色，conf_security 消费",
    "SCIM_ENABLED": "SCIM 开关，conf_security + scim/auth 消费",
    "SCIM_RATE_LIMIT": "SCIM 限流，conf_security + scim/auth 消费",
    "SENSITIVE_OPERATION_METHODS": "敏感操作方法，conf_security + 告警消费",
    "SENSITIVE_OPERATION_PATHS": "敏感操作路径，approval/auth_scopes 消费",
    # common/core/config/conf_ops.py（注入面：保留期/异步并发/会话/监控）
    "ACCOUNT_EXPIRY_REMIND_DAYS": "到期提醒天数，conf_ops 消费",
    "CHAT_HISTORY_DAYS": "聊天历史保留，conf_ops 消费",
    "EXPORT_ASYNC_MAX_RUNNING": "导出并发上限，conf_ops 消费",
    "EXPORT_FILE_KEEP_DAYS": "导出文件保留，conf_ops 消费",
    "FILE_KEEP_DAYS": "文件保留天数，conf_ops/conf_upload 消费",
    "IMPORT_ASYNC_MAX_RUNNING": "导入并发上限，conf_ops 消费",
    "IMPORT_FAIL_RATE_LIMIT": "导入失败限流，conf_ops 消费",
    "IMPORT_RECORD_KEEP_DAYS": "导入记录保留，conf_ops 消费",
    "IMPORT_VALIDATE_ERROR_LIMIT": "导入校验错误上限，conf_ops 消费",
    "LOGIN_LOG_RETENTION_DAYS": "登录日志保留，conf_ops 消费",
    "MONITOR_RETENTION_DAYS": "监控数据保留，conf_ops 消费",
    "OPERATION_LOG_ERROR_RETENTION_DAYS": "错误操作日志保留，conf_ops 消费",
    "OPERATION_LOG_FIELD_MAX": "操作日志字段截断，conf_ops 消费",
    "OPERATION_LOG_RETENTION_DAYS": "操作日志保留，conf_ops 消费",
    "SEARCH_CHOICES_MAX_COUNT": "搜索候选上限，conf_ops 消费",
    "SESSION_ONLINE_TIMEOUT": "在线会话超时，conf_ops 消费",
    "USER_SESSION_RETENTION_DAYS": "会话记录保留，conf_ops 消费",
    "WEB_SITE_URL": "站点外链，conf_ops + user_invite 消费",
    # common/core/config/conf_upload.py（注入面：文件存储/S3/预览）
    "FILE_OFFICE_CONVERT_TIMEOUT": "office 转换超时，conf_upload 消费",
    "FILE_OFFICE_MAX_BYTES": "office 预览大小上限，conf_upload 消费",
    "FILE_OFFICE_PREVIEW_ENABLED": "office 预览开关，conf_upload 消费",
    "FILE_OFFICE_SOFFICE_BIN": "soffice 路径，conf_upload 消费",
    "FILE_OFFICE_WAIT_SECONDS": "office 转换等待，conf_upload 消费",
    "FILE_PREVIEW_CACHE_KEEP_DAYS": "预览缓存保留，conf_upload 消费",
    "FILE_PREVIEW_IMAGE_WIDTH": "预览图宽度，conf_upload 消费",
    "FILE_PREVIEW_TEXT_MAX_BYTES": "文本预览截断，conf_upload 消费",
    "FILE_PREVIEW_THUMB_WIDTH": "缩略图宽度，conf_upload 消费",
    "FILE_S3_ACCESS_KEY": "S3 凭据，conf_upload + credentials 消费",
    "FILE_S3_ADDRESSING_STYLE": "S3 寻址风格，conf_upload + storage 消费",
    "FILE_S3_BUCKET": "S3 桶名，conf_upload + storage 消费",
    "FILE_S3_CUSTOM_DOMAIN": "S3 自定义域名，conf_upload + storage 消费",
    "FILE_S3_ENDPOINT": "S3 端点，conf_upload + storage 消费",
    "FILE_S3_REGION": "S3 区域，conf_upload + storage 消费",
    "FILE_S3_SECRET_KEY": "S3 密钥，conf_upload + credentials 消费",
    "FILE_STORAGE_BACKEND": "存储后端选择，conf_upload + storage 消费",
    "FILE_STORAGE_QUOTA_MB": "存储配额，conf_upload + upload_store 消费",
    "FILE_UPLOAD_COUNT_LIMIT": "上传数量限制，conf_upload + upload_store 消费",
    # common/core/config/system_conf.py（注入面：推送开关）
    "PUSH_CHAT_MESSAGE": "聊天推送开关，system_conf + configs 视图消费",
    "PUSH_MESSAGE_NOTICE": "通知推送开关，system_conf + configs 视图消费",
}
