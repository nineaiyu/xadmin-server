#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#

import errno
import json
import logging
import os
import sys
import types
from importlib import import_module

import yaml

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger("xadmin.conf")


def import_string(dotted_path):
    try:
        module_path, class_name = dotted_path.rsplit(".", 1)
    except ValueError as err:
        raise ImportError(f"{dotted_path} doesn't look like a module path") from err

    module = import_module(module_path)

    try:
        return getattr(module, class_name)
    except AttributeError as err:
        raise ImportError(f'Module "{module_path}" does not define a "{class_name}" attribute/class') from err


class DoesNotExist(Exception):
    pass


class Config(dict):
    base = {
        "SECRET_KEY": "",
        "DEBUG": False,
        "DEBUG_DEV": False,
        # django-silk 性能剖析开关（性能基线）：仅允许 DEBUG/DEBUG_DEV 环境开启，
        # 依赖在 requirements-dev.txt（django-silk）；开启后需执行 migrate 创建 silk 表
        "SILK_ENABLED": False,
        # Prometheus 指标（默认关闭）：启用需同时配置 METRICS_TOKEN，
        # 抓取方以 Authorization: Bearer <token> 访问 /api/common/api/metrics
        "METRICS_ENABLED": False,
        "METRICS_TOKEN": "",
        "LOG_LEVEL": "WARNING",
        # 应用日志格式 text（默认）/ json（结构化，供 Loki/ELK 采集）
        "LOG_FORMAT": "text",
        # 按天滚动的日志保留天数，超出后整体清理日期目录（0 表示不清理）
        "LOG_BACKUP_COUNT": 30,
        # Sentry 错误聚合；DSN 为空时完全不初始化（sentry-sdk 已在 requirements.txt）
        "SENTRY_DSN": "",
        "SENTRY_ENVIRONMENT": "production",
        "SENTRY_TRACES_SAMPLE_RATE": 0.0,
        "XADMIN_APPS": [],
        # ------------------------------------------------------------------
        # 功能模块裁剪（软裁剪）：预设 + 显式增删，模块清单与裁剪语义见
        # common/core/modules.py；默认 full = 全部开启，与改造前行为一致
        # ------------------------------------------------------------------
        "MODULE_PRESET": "full",  # core / standard / full
        "MODULE_ENABLE": [],  # 在预设基础上额外启用，如 ["chat"]
        "MODULE_DISABLE": [],  # 在预设基础上关闭，如 ["analysis", "chat"]
        # 表前缀 abc_
        "DB_PREFIX": "",
        # redis
        "REDIS_HOST": "redis",
        "REDIS_PORT": 6379,
        "REDIS_PASSWORD": "",
        "DEFAULT_CACHE_ID": 1,
        "CHANNEL_LAYERS_CACHE_ID": 2,
        "CELERY_BROKER_CACHE_ID": 3,
        # database
        "DB_ENGINE": "mysql",
        "DB_HOST": "mariadb",
        "DB_PORT": 3306,
        "DB_DATABASE": "xadmin",
        "DB_USER": "server",
        "DB_PASSWORD": "",
        # PostgreSQL server 端连接池（Django 5.1+，需 psycopg3）。
        # 仅 DB_ENGINE=postgresql 生效；开启后 CONN_MAX_AGE 自动归零（连接生命周期由池管理）。
        # 容量核算：GUNICORN_MAX_WORKER × DB_POOL_MAX_SIZE + celery 子进程数 × DB_POOL_MAX_SIZE
        # 应小于 PG max_connections
        "DB_POOL": True,
        "DB_POOL_MIN_SIZE": 2,
        "DB_POOL_MAX_SIZE": 8,
        # HOST 校验白名单，生产环境必须配置，如 ['xadmin.example.com']；DEBUG 模式默认放行
        "ALLOWED_HOSTS": [],
        # 反向代理信任清单（单个 IP 或 CIDR 字符串数组）。
        # 仅当直连地址（REMOTE_ADDR）命中清单时，才按 X-Forwarded-For 解析客户端真实 IP
        # （从右往左取第一个非可信地址）；直连地址不在清单内 = 请求头不可信，直接用直连地址，
        # 防止伪造 XFF 绕过登录 IP 封禁 / PAT IP 白名单 / 污染登录日志。默认空 = 完全不信任 XFF。
        # 注意：仅当代理以 HTTP 反代方式注入 XFF（如 xadmin-web 内置 nginx 的
        # $proxy_add_x_forwarded_for）才可配置；纯 TCP stream 代理不注入 XFF，
        # 为其配置会引入伪造面。示例：['192.168.196.0/24']
        "TRUSTED_PROXY_IPS": [],
        # CORS 跨域配置，同源部署（nginx 反代）无需配置；跨域部署请配置白名单
        "CORS_ALLOW_ALL_ORIGINS": False,
        "CORS_ALLOWED_ORIGINS": [],
        "LANGUAGE_CODE": "zh-hans",
        "TIME_ZONE": "Asia/Shanghai",
        # 服务配置
        "HTTP_BIND_HOST": "0.0.0.0",
        "HTTP_LISTEN_PORT": 8896,
        "GUNICORN_MAX_WORKER": 4,
        "CELERY_WORKER_COUNT": 10,
        # heavy 队列（导入/导出/批量重任务）worker 配置。
        # CPU 密集的 Excel 导出可把 POOL 改为 'prefork' 提升吞吐（threads 池受 GIL 限制）；
        # 默认维持 threads，与 default 队列保持相同的运行时状态共享行为
        "CELERY_HEAVY_POOL": "threads",
        "CELERY_HEAVY_CONCURRENCY": 4,
        # DRF BasicAuthentication 总开关：base64 明文凭证，默认关闭；
        # 本地调试需要时在 config.yml 显式开启
        "BASIC_AUTH_ENABLED": False,
        # celery flower 任务监控配置
        "CELERY_FLOWER_PORT": 5566,
        "CELERY_FLOWER_HOST": "127.0.0.1",
        # Flower 监控 basic-auth（格式 用户:密码），生产环境必须配置；
        # 未配置时 Flower 仅允许绑定 127.0.0.1 供本机调试，绑定其他地址将拒绝启动
        "CELERY_FLOWER_AUTH": "",
        # LDAP/AD 目录同步：默认全关，行为与无 LDAP 时完全一致。
        # 运行期经 settings app 的 Setting 体系（category=ldap）热更新覆盖
        "LDAP_AUTH_ENABLED": False,
        # ldap_first：先 bind 目录（本地密码兜底）；local_first：本地可用密码优先（防目录密码遮蔽本地管理员）
        "LDAP_AUTH_PRIORITY": "local_first",
        "LDAP_AUTH_AUTO_CREATE": True,
        "LDAP_SERVER_URI": "",
        "LDAP_START_TLS": False,
        "LDAP_BIND_DN": "",
        # 值级加密落库（Setting.encrypted），此处仅默认值
        "LDAP_BIND_PASSWORD": "",
        "LDAP_CONNECT_TIMEOUT": 10,
        "LDAP_USER_SEARCH_BASE": "",
        "LDAP_USER_FILTER": "(objectClass=person)",
        # 字段映射（固定四键，管理页可改）：目录属性名 -> 平台字段
        "LDAP_ATTR_USERNAME": "sAMAccountName",
        "LDAP_ATTR_NICKNAME": "cn",
        "LDAP_ATTR_EMAIL": "mail",
        "LDAP_ATTR_PHONE": "telephoneNumber",
        "LDAP_DEPT_ENABLED": True,
        "LDAP_DEPT_SEARCH_BASE": "",
        "LDAP_SYNC_ENABLED": False,
        # 组同步（审批流外的 LDAP 三期项）：读取用户所属组的属性名（AD 默认 memberOf）
        "LDAP_ATTR_GROUPS": "memberOf",
        # 组 → 平台角色 code 映射（键为组 DN 或 CN，大小写不敏感；空 = 不启用组映射）
        "LDAP_GROUP_ROLE_MAP": {},
        "LDAP_SYNC_AUTO_CREATE": True,
        # 目录侧消失策略：deactivate 禁用（默认，可逆）/ soft_delete 进回收站 / ignore 不处理
        "LDAP_SYNC_MISSING_POLICY": "deactivate",
        # 同步分页大小（ldap3 paged search）
        "LDAP_SYNC_PAGE_SIZE": 500,
        # 企业 IM 通知渠道：默认全关；运行期经 Setting 体系（category=notify_im）
        # 热更新覆盖，secret 值级加密落库。开关开而凭据缺 → 渠道自动降级为不可用
        "DINGTALK_ENABLED": False,
        "DINGTALK_APP_KEY": "",
        "DINGTALK_APP_SECRET": "",
        "DINGTALK_AGENT_ID": "",
        "WECOM_ENABLED": False,
        "WECOM_CORP_ID": "",
        "WECOM_CORP_SECRET": "",
        "WECOM_AGENT_ID": "",
        "FEISHU_ENABLED": False,
        "FEISHU_APP_ID": "",
        "FEISHU_APP_SECRET": "",
        # AI 助手：OpenAI 兼容协议，默认全关；API Key 值级加密落库。
        # 多档案（AiProfile）为主通路：激活档案供全部 AI 链路使用；
        # 以下键是「无激活档案时」的 Setting 回落默认值（category=ai 可热更新覆盖）
        "AI_ASSISTANT_ENABLED": False,
        "AI_BASE_URL": "",
        "AI_API_KEY": "",
        "AI_MODEL": "gpt-4o-mini",
        "AI_TIMEOUT": 60,
        # AI 二期 NL 查数：默认关闭灰度
        "AI_NL_QUERY_ENABLED": False,
        # AI 四期受限动作（A2：草稿→确认→以用户身份执行）：默认关闭灰度，
        # 白名单动作与审计见 system/utils/ai_actions.py
        "AI_ACTION_ENABLED": False,
        # AI 三期采样/行为参数（档案未配置的参数按此回落；None = 不下发走供应商默认）
        "AI_TEMPERATURE": 0.2,
        "AI_MAX_TOKENS": 0,
        "AI_TOP_P": None,
        "AI_FREQUENCY_PENALTY": None,
        "AI_PRESENCE_PENALTY": None,
        "AI_STOP": "",
        "AI_SEED": None,
        "AI_MAX_RETRIES": 0,
        "AI_CONTEXT_LIMIT": 20,
        "AI_PERSONA": "",
    }
    libs = {
        # REST_FRAMEWORK
        "DEFAULT_THROTTLE_RATES": {},
        # SIMPLE_JWT
        "ACCESS_TOKEN_LIFETIME": 3600,  # Unit: second
        "REFRESH_TOKEN_LIFETIME": 15 * 24 * 3600,  # Unit: second
    }
    settings = {
        # 密码安全配置
        "SECURITY_PASSWORD_MIN_LENGTH": 10,
        "SECURITY_ADMIN_USER_PASSWORD_MIN_LENGTH": 6,
        "SECURITY_PASSWORD_UPPER_CASE": True,
        "SECURITY_PASSWORD_LOWER_CASE": False,
        "SECURITY_PASSWORD_NUMBER": True,
        "SECURITY_PASSWORD_SPECIAL_CHAR": False,
        # 泄露密码库校验（内置离线弱口令清单，命中即拒绝；改密/注册/重置等链路接入；
        # 2026-09-12 灰度转正：评审确认开启，误伤反馈走 settings/data/leak_passwords.txt 清单维护）
        "SECURITY_PASSWORD_LEAK_CHECK_ENABLED": True,
        # 密码历史：最近 N 次不可复用（改密/重置时留存哈希；0 = 关闭校验；
        # 2026-09-12 灰度转正：评审确认 N=3）
        "SECURITY_PASSWORD_HISTORY_COUNT": 3,
        # 密码有效期（天数，超期登录被拒并提示改密；0 = 永不过期。
        # 存量用户 date_password_updated 为空 = 宽限期不拦截，改密后开始计时；
        # 2026-09-12 灰度转正：评审确认 90 天）
        "SECURITY_PASSWORD_EXPIRATION_DAYS": 90,
        # AES 旧格式（Salted__）解密灰度开关：默认开启保持存量前端兼容；
        # 确认全量用户已升级至 v2 优先前端后可关闭，关闭后旧格式一律按非法输入拒绝（返回空串）
        "SECURITY_AES_V1_DECRYPT_ENABLED": True,
        # HTTPS 部署安全头（默认关闭 = 现有 HTTP 直连部署零影响）：
        # 开启后强制 Secure Cookie（Session/CSRF）+ HSTS（1 年，含子域/preload）+ nosniff。
        # 仅在 TLS 终止于反向代理/网关且对外入口为 HTTPS 时开启
        "SECURITY_HTTPS_ENABLED": False,
        # 是否由 Django 把 HTTP 请求 301 重定向到 HTTPS。
        # 仅当代理层（HTTP 反代）会正确传递 X-Forwarded-Proto: https 时才可开启；
        # 纯 TCP stream 代理（内置 nginx 默认形态）下开启会造成重定向循环，保持关闭
        "SECURITY_HTTPS_REDIRECT_ENABLED": False,
        # 用户登录限制的规则
        "SECURITY_LOGIN_LIMIT_COUNT": 7,
        "SECURITY_LOGIN_LIMIT_TIME": 30,  # Unit: minute
        "SECURITY_CHECK_DIFFERENT_CITY_LOGIN": True,
        # 新设备/新 IP 登录提醒（异常登录提醒第二维度，与异地城市提醒互补；默认关闭渐进启用）
        "SECURITY_ABNORMAL_LOGIN_ALERT_ENABLED": False,
        "SECURITY_LOGIN_BASELINE_DAYS": 30,  # 判定基线窗口（近 N 天成功登录历史）
        # 登录IP限制的规则
        "SECURITY_LOGIN_IP_BLACK_LIST": [],
        "SECURITY_LOGIN_IP_WHITE_LIST": [],
        "SECURITY_LOGIN_IP_LIMIT_COUNT": 50,
        "SECURITY_LOGIN_IP_LIMIT_TIME": 30,  # Unit: minute
        # 登陆规则
        "SECURITY_LOGIN_ACCESS_ENABLED": True,
        "SECURITY_LOGIN_CAPTCHA_ENABLED": True,
        "SECURITY_LOGIN_ENCRYPTED_ENABLED": True,
        "SECURITY_LOGIN_TEMP_TOKEN_ENABLED": True,
        "SECURITY_LOGIN_BY_EMAIL_ENABLED": True,
        "SECURITY_LOGIN_BY_SMS_ENABLED": False,
        "SECURITY_LOGIN_BY_BASIC_ENABLED": True,
        # 注册规则
        "SECURITY_REGISTER_ACCESS_ENABLED": True,
        "SECURITY_REGISTER_CAPTCHA_ENABLED": True,
        "SECURITY_REGISTER_ENCRYPTED_ENABLED": True,
        "SECURITY_REGISTER_TEMP_TOKEN_ENABLED": True,
        "SECURITY_REGISTER_BY_EMAIL_ENABLED": True,
        "SECURITY_REGISTER_BY_SMS_ENABLED": False,
        "SECURITY_REGISTER_BY_BASIC_ENABLED": True,
        # 忘记密码规则
        "SECURITY_RESET_PASSWORD_ACCESS_ENABLED": True,
        "SECURITY_RESET_PASSWORD_CAPTCHA_ENABLED": True,
        "SECURITY_RESET_PASSWORD_TEMP_TOKEN_ENABLED": True,
        "SECURITY_RESET_PASSWORD_ENCRYPTED_ENABLED": True,
        "SECURITY_RESET_PASSWORD_BY_EMAIL_ENABLED": True,
        "SECURITY_RESET_PASSWORD_BY_SMS_ENABLED": False,
        # 绑定邮箱
        "SECURITY_BIND_EMAIL_ACCESS_ENABLED": True,
        "SECURITY_BIND_EMAIL_CAPTCHA_ENABLED": True,
        "SECURITY_BIND_EMAIL_TEMP_TOKEN_ENABLED": True,
        "SECURITY_BIND_EMAIL_ENCRYPTED_ENABLED": True,
        # 绑定手机
        "SECURITY_BIND_PHONE_ACCESS_ENABLED": True,
        "SECURITY_BIND_PHONE_CAPTCHA_ENABLED": True,
        "SECURITY_BIND_PHONE_TEMP_TOKEN_ENABLED": True,
        "SECURITY_BIND_PHONE_ENCRYPTED_ENABLED": True,
        # MFA / 敏感操作二次验证
        "SECURITY_MFA_CONFIRM_ENABLED": True,  # 敏感操作二次验证总开关
        "SECURITY_MFA_CONFIRM_BACKENDS": ["otp", "sms", "email", "password"],  # 允许的验证方式
        "SECURITY_MFA_VERIFY_TTL": 3600,  # MFA 方式确认有效期（秒）
        "SECURITY_MFA_PASSWORD_CONFIRM_TTL": 300,  # 密码方式确认有效期（秒）
        "SECURITY_MFA_LOGIN_PROTECT_ENABLED": True,  # 绑定 OTP 的用户登录时强制二次验证
        "SECURITY_MFA_LOGIN_TOKEN_TTL": 300,  # 登录 MFA 临时令牌有效期（秒）
        "SECURITY_MFA_OTP_VALID_WINDOW": 1,  # OTP 容错窗口（前后各 N 个周期）
        "SECURITY_MFA_OTP_ISSUER": "XAdmin",  # OTP 绑定 URI 中的签发方名称
        # 资源告警阈值（check_server_performance_period 周期检查，超标时邮件/站内信通知超管）
        "SECURITY_MONITOR_DISK_USED_MAX": 80,  # 磁盘使用率阈值（%）
        "SECURITY_MONITOR_MEMORY_USED_MAX": 85,  # 内存使用率阈值（%）
        "SECURITY_MONITOR_CPU_PERCENT_MAX": 80,  # CPU 使用率阈值（%）
        "SECURITY_MONITOR_CPU_LOAD_MAX": 5,  # 单核 CPU 负载阈值
        # 基本配置
        "SITE_URL": "http://127.0.0.1:8000",
        "FRONT_END_WEB_WATERMARK_ENABLED": False,  # 前端水印展示
        "FRONT_END_WEB_WATERMARK_TEXT": "",  # 前端水印文案（留空 = 用户名-昵称-时间）
        "FRONT_END_WEB_WATERMARK_PATHS": "",  # 前端水印生效页面（逗号分隔路由前缀，留空 = 全部页面）
        "PERMISSION_FIELD_ENABLED": True,  # 字段权限控制
        "PERMISSION_DATA_ENABLED": True,  # 数据权限控制
        "REFERER_CHECK_ENABLED": False,  # referer 校验
        "EXPORT_MAX_LIMIT": 20000,  # 限制导出数据数量
        # 异步导出记录与产物保留天数（下载中心），超期由 auto_clean_export_record_job 清理
        "EXPORT_FILE_KEEP_DAYS": 7,
        # 异步导入记录/源文件/错误报告保留天数（下载中心），超期由 auto_clean_import_record_job 清理
        "IMPORT_RECORD_KEEP_DAYS": 30,
        "IMPORT_FAIL_RATE_LIMIT": 0.5,  # 异步导入失败率中止阈值；0 表示不按失败率中止
        "IMPORT_ASYNC_MAX_RUNNING": 3,  # 同一用户同时进行中的异步导入任务上限；0 表示不限制
        "IMPORT_VALIDATE_ERROR_LIMIT": 200,  # 导入前校验返回的错误行明细上限
        # 软删除回收站保留天数，超过后由 purge_soft_deleted 周期任务物理清除
        "RECYCLE_BIN_RETENTION_DAYS": 30,
        # 敏感操作审批：拦截路径正则清单（默认空 = 休眠，渐进启用；仅对显式挂载
        # ApprovalRequired 装饰器的 action 生效），审批通过后携一次性令牌重发放行
        "APPROVAL_REQUIRED_PATHS": [],
        # 审批动作需 MFA 二次确认的清单（默认空 = 不启用；审批流三期）：
        # 取值 approve / reject / cancel / add_sign / batch_approve / batch_reject，
        # 命中动作在业务变更前走 412（user_confirm_required）协议
        "APPROVAL_MFA_REQUIRED_ACTIONS": [],
        # 审批人角色 code 清单（默认空 = 全部在用超管；申请人不能自审）
        "APPROVAL_APPROVER_ROLES": [],
        # 审批通过后令牌有效期（秒）
        "APPROVAL_TOKEN_TTL": 300,
        # 待审批单超时天数（超时由 auto_expire_approval_job 置 EXPIRED）
        "APPROVAL_PENDING_TIMEOUT": 3,
        # 审批单保留天数（超过由 auto_clean_approval_job 分批删除）
        "APPROVAL_KEEP_DAYS": 180,
        # 待审批超时提醒阈值（小时）：由每日提醒任务对未处理的单补发一次提醒；0 = 不提醒
        "APPROVAL_REMIND_HOURS": 24,
        # 流程实例（全量审批流引擎）保留天数：超过由清理任务分批删除
        "APPROVAL_FLOW_KEEP_DAYS": 365,
        # 请假审批流程 code：请假单提交时绑定的流程定义；该 code 不存在时
        # 依次回退 leave_<请假类型> 与「leave 前缀的启用流程」
        "LEAVE_APPROVAL_FLOW_CODE": "leave",
        # Office 在线预览：LibreOffice headless 转 PDF 后内嵌渲染
        "FILE_OFFICE_PREVIEW_ENABLED": True,  # 关闭或未安装转换器时按「不支持预览」降级
        "FILE_OFFICE_MAX_BYTES": 20 * 1024 * 1024,  # 转换大小上限（字节，默认 20MB）
        "FILE_OFFICE_CONVERT_TIMEOUT": 60,  # 单次转换超时（秒）
        "FILE_OFFICE_WAIT_SECONDS": 8,  # 请求侧等待产物窗口（秒）：未等到回 1006 由前端重试
        "FILE_OFFICE_SOFFICE_BIN": "",  # 转换器路径：空 = 自动探测 PATH 与常见安装路径
        # SCIM 2.0 用户目录同步（S1）：默认整体休眠，需显式开启并配置独立 Bearer Token
        "SCIM_ENABLED": False,
        "SCIM_TOKEN": "",
        "SCIM_RATE_LIMIT": "600/min",  # 凭证级限流；空或 0 = 不限
        "SCIM_DEFAULT_ROLE_CODE": "",  # 新建用户默认角色 code（空 = 不分配）
        # CSP（S3）：django-csp 生成策略，模式与上报地址运行期可配
        "CSP_MODE": "report-only",  # disabled / report-only（观察期）/ enforce
        "CSP_REPORT_URI": "",  # 空 = 不下发 report-uri；建议 /api/common/api/csp-report
        # PAT 凭证级限流速率（SimpleRateThrottle 速率串；空或 0 = 不限）
        "PAT_RATE_LIMIT": "60/min",
        # 单文件上传大小上限（字节，默认 10MB）：与 DB 种子 loadjson/systemconfig.json 对齐，
        # 裸环境（未执行 load_init_json）兜底，避免 upload 动作拿 None 比较直接 500
        "FILE_UPLOAD_SIZE": 10 * 1024 * 1024,
        # 图片上传大小上限（字节，默认 500KB）：同上
        "PICTURE_UPLOAD_SIZE": 512 * 1024,
        # 个人文件存储配额（MB；0 = 不限）
        "FILE_STORAGE_QUOTA_MB": 0,
        # 个人上传文件数量上限（0 = 不限）
        "FILE_UPLOAD_COUNT_LIMIT": 0,
        # 正式上传文件保留天数（0 = 不清理）：仅清理非临时、无业务引用的历史文件
        "FILE_KEEP_DAYS": 0,
        # 文本预览读取上限（字节）：超出即截断并提示下载查看
        "FILE_PREVIEW_TEXT_MAX_BYTES": 256 * 1024,
        # 预览缩略图宽度（像素）：列表行内缩略图 / 抽屉大图
        "FILE_PREVIEW_THUMB_WIDTH": 240,
        "FILE_PREVIEW_IMAGE_WIDTH": 1280,
        # 预览缓存保留天数：派生产物，过期删除后按需重建
        "FILE_PREVIEW_CACHE_KEEP_DAYS": 7,
        # 第三方登录 provider 列表（空 = 整体休眠，登录页不显示第三方入口）
        "OAUTH_PROVIDERS": [],
        # 字段级审计 diff 白名单（模型 _meta.label）：命中白名单的 update 请求会额外
        # 做 2 次查询以计算 old/new。默认开「用户管理」——它是当前唯一挂了「变更历史」
        # 入口的页面（changeHistory:SystemUser 权限菜单），关闭此项会让变更明细恒为
        # 空（前端显示「—」）；新增带入口的页面时在此追加，置空数组 = 整体关闭
        "AUDIT_DIFF_MODELS": ["system.UserInfo"],
        # 验证码配置
        "VERIFY_CODE_TTL": 5 * 60,  # Unit: second
        "VERIFY_CODE_LIMIT": 60,
        "VERIFY_CODE_LENGTH": 6,
        "VERIFY_CODE_LOWER_CASE": False,
        "VERIFY_CODE_UPPER_CASE": False,
        "VERIFY_CODE_DIGIT_CASE": True,
        # 邮件配置
        "EMAIL_ENABLED": False,
        "EMAIL_HOST": "",
        "EMAIL_PORT": 465,
        "EMAIL_HOST_USER": "",
        "EMAIL_HOST_PASSWORD": "",
        "EMAIL_FROM": "",
        "EMAIL_RECIPIENT": "",
        "EMAIL_SUBJECT_PREFIX": "Xadmin-Server ",
        "EMAIL_USE_SSL": True,
        "EMAIL_USE_TLS": False,
        # 短信配置
        "SMS_ENABLED": False,
        "SMS_BACKEND": "alibaba",
        "SMS_TEST_PHONE": "",
        # 短信通知模板（正文走「通知模板 + 单变量」发送；签名/模板未配置时短信通知渠道自动降级为不可用）
        "SMS_NOTIFY_SIGN_NAME": "",
        "SMS_NOTIFY_TEMPLATE_CODE": "",
        "SMS_NOTIFY_TEMPLATE_PARAM_KEY": "content",
        # 阿里云短信配置
        "ALIBABA_ACCESS_KEY_ID": "",
        "ALIBABA_ACCESS_KEY_SECRET": "",
        "ALIBABA_VERIFY_SIGN_NAME": "",
        "ALIBABA_VERIFY_TEMPLATE_CODE": "",
        # 图片验证码
        "CAPTCHA_IMAGE_SIZE": (120, 40),  # 设置 captcha 图片大小
        "CAPTCHA_CHALLENGE_FUNCT": "captcha.helpers.math_challenge",
        "CAPTCHA_LENGTH": 4,  # 字符个数,仅针对随机字符串生效
        "CAPTCHA_TIMEOUT": 5,  # 超时(minutes)
        "CAPTCHA_FONT_SIZE": 26,
        "CAPTCHA_BACKGROUND_COLOR": "#ffffff",
        "CAPTCHA_FOREGROUND_COLOR": "#001100",
        "CAPTCHA_NOISE_FUNCTIONS": ("captcha.helpers.noise_arcs", "captcha.helpers.noise_dots"),
        # ------------------------------------------------------------------
        # 运行期系统配置（SysConfig 热更新）的代码默认值唯一源：
        # common/core/config.py 的 SysConfig property 一律回读这里（CONFIG.<KEY>），
        # loadjson/systemconfig.json 的初始值须与本段一致
        # （守护测试 tests/unit/common/test_config_defaults_single_source.py）
        # ------------------------------------------------------------------
        # 消息推送开关（用户级可再覆盖）
        "PUSH_MESSAGE_NOTICE": True,
        "PUSH_CHAT_MESSAGE": True,
        # 操作日志保留天数（清理任务按此分批删除）；错误日志额外保留天数（0/空 = 跟随全量）
        "OPERATION_LOG_RETENTION_DAYS": 180,
        "OPERATION_LOG_ERROR_RETENTION_DAYS": 365,
        # 敏感操作告警：方法清单（"ALL" 或空 = 不按方法过滤）与路径正则清单（空 = 不按路径过滤）
        "SENSITIVE_OPERATION_METHODS": ["DELETE"],
        "SENSITIVE_OPERATION_PATHS": [],
        # 慢请求阈值（秒）：超阈值打 WARNING 日志，监控面板 slow 接口同口径
        "SLOW_REQUEST_THRESHOLD": 1.0,
        # search-columns / search-fields 关联列 choices 最大返回条数
        "SEARCH_CHOICES_MAX_COUNT": 200,
        # 同一用户同时进行中的异步导出任务上限（0 = 不限）
        "EXPORT_ASYNC_MAX_RUNNING": 3,
        # 主机监控心跳历史保留天数（30s 一条长期落库）
        "MONITOR_RETENTION_DAYS": 30,
        # 纯 HTTP 会话在线判定窗口（秒）/ 已结束会话记录保留天数
        "SESSION_ONLINE_TIMEOUT": 300,
        "USER_SESSION_RETENTION_DAYS": 30,
        # 聊天消息保留天数（0 = 不清理）
        "CHAT_HISTORY_DAYS": 0,
        # 审批人职能权限码清单（与 APPROVAL_APPROVER_ROLES 取并集；两者皆空 = 全部在用超管）
        "APPROVAL_APPROVER_PERMS": [],
        # 备份失败告警回调令牌（空 = 端点未启用）
        "BACKUP_ALERT_TOKEN": "",
    }

    defaults = {
        "API_LOG_ENABLE": True,
        # 忽略日志记录, 支持model 或者 request_path, 不支持正则
        "API_LOG_IGNORE": {
            "system.OperationLog": ["GET"],
            "/api/common/api/health": ["GET"],
        },
        "API_LOG_METHODS": ["POST", "DELETE", "PUT", "PATCH"],
        "API_MODEL_MAP": {
            "/api/system/refresh": "Token刷新",
            "/api/flower": "定时任务",
        },
    }
    defaults.update(base)
    defaults.update(libs)
    defaults.update(settings)
    old_config_map = {}

    def __init__(self, *args):
        super().__init__(*args)

    def convert_type(self, k, v):
        default_value = self.defaults.get(k)
        if default_value is None:
            return v
        tp = type(default_value)
        # 对bool特殊处理
        if tp is bool and isinstance(v, str):
            if v.lower() in ("true", "1"):
                return True
            else:
                return False
        if tp in [list, dict] and isinstance(v, str):
            try:
                v = json.loads(v)
                return v
            except json.JSONDecodeError:
                return v

        try:
            if tp in [list, dict]:
                v = json.loads(v)
            else:
                v = tp(v)
        except Exception:
            pass
        return v

    def __repr__(self):
        return f"<{self.__class__.__name__} {dict.__repr__(self)}>"

    def get_from_config(self, item):
        try:
            value = super().__getitem__(item)
        except KeyError:
            value = None
        return value

    def get_from_env(self, item):
        value = os.environ.get(item, None)
        if value is not None:
            value = self.convert_type(item, value)
        return value

    def get(self, item, default=None):
        # 再从配置文件中获取
        value = self.get_from_config(item)
        if value is None:
            value = self.get_from_env(item)

        # 因为要递归，所以优先从上次返回的递归中获取
        if default is None:
            default = self.defaults.get(item)
        if value is None and item in self.old_config_map:
            return self.get(self.old_config_map[item], default)
        if value is None:
            value = default
        return value

    def __getitem__(self, item):
        return self.get(item)

    def __getattr__(self, item):
        return self.get(item)


class ConfigManager:
    config_class = Config

    def __init__(self, root_path=None):
        self.root_path = root_path
        self.config = self.config_class()

    def from_pyfile(self, filename="config.py", silent=False):

        if self.root_path:
            filename = os.path.join(self.root_path, filename)
        d = types.ModuleType("config")
        d.__file__ = filename
        try:
            with open(filename, mode="rb") as config_file:
                exec(compile(config_file.read(), filename, "exec"), d.__dict__)
        except OSError as e:
            if silent and e.errno in (errno.ENOENT, errno.EISDIR):
                return False
            e.strerror = f"Unable to load configuration file ({e.strerror})"
            return False
        self.from_object(d)
        return True

    def from_object(self, obj):
        if isinstance(obj, str):
            obj = import_string(obj)
        for key in dir(obj):
            if key.isupper():
                self.config[key] = getattr(obj, key)

    def from_json(self, filename, silent=False):
        if self.root_path:
            filename = os.path.join(self.root_path, filename)
        try:
            with open(filename) as json_file:
                obj = json.loads(json_file.read())
        except OSError as e:
            if silent and e.errno in (errno.ENOENT, errno.EISDIR):
                return False
            e.strerror = f"Unable to load configuration file ({e.strerror})"
            raise
        return self.from_mapping(obj)

    def from_yaml(self, filename, silent=False):
        if self.root_path:
            filename = os.path.join(self.root_path, filename)
        try:
            with open(filename, encoding="utf8") as f:
                obj = yaml.safe_load(f)
        except OSError as e:
            if silent and e.errno in (errno.ENOENT, errno.EISDIR):
                return False
            e.strerror = f"Unable to load configuration file ({e.strerror})"
            raise
        if obj:
            return self.from_mapping(obj)
        return True

    def from_mapping(self, *mapping, **kwargs):
        mappings = []
        if len(mapping) == 1:
            if hasattr(mapping[0], "items"):
                mappings.append(mapping[0].items())
            else:
                mappings.append(mapping[0])
        elif len(mapping) > 1:
            raise TypeError(f"expected at most 1 positional argument, got {len(mapping)}")
        mappings.append(kwargs.items())
        for mapping in mappings:
            for key, value in mapping:
                if key.isupper():
                    self.config[key] = value
        return True

    def load_from_object(self):
        sys.path.insert(0, PROJECT_DIR)
        try:
            from config import config as c
        except ImportError:
            return False
        if c:
            self.from_object(c)
            return True
        else:
            return False

    def load_from_yml(self):
        for i in ["config.yml", "config.yaml"]:
            if not os.path.isfile(os.path.join(self.root_path, i)):
                continue
            loaded = self.from_yaml(i)
            if loaded:
                return True
        return False

    @classmethod
    def load_user_config(cls, root_path=None, config_class=None):
        config_class = config_class or Config
        cls.config_class = config_class
        if not root_path:
            root_path = PROJECT_DIR

        manager = cls(root_path=root_path)
        if manager.from_pyfile():
            config = manager.config
        elif manager.load_from_object():
            config = manager.config
        elif manager.load_from_yml():
            config = manager.config
        else:
            msg = """

            Error: No config file found.

            You can run `cp config_example.yml config.yml`, and edit it.
            """
            raise ImportError(msg)

        return config
