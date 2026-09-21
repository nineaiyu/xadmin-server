#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""服务器配置默认值（静态字典部分：通用/基础 + 三方库）。"""

BASE_CONFIG = {
    "SECRET_KEY": "",
    # SECRET_KEY 缺失时的自动生成开关（开箱即用，见 server/conf/manager.py）：
    # - 无任何配置文件（回落 config_example.yml）或 DEBUG=true 时默认自动生成并持久化到 data/.secret_key；
    # - 显式 true 强制开启；显式 false（默认）且非上述场景时，缺失 SECRET_KEY 按生产口径拒绝启动；
    # - 自动密钥仅限开发/首次体验，生产必须显式配置（多实例一致性 + 已加密数据可解性）。
    "SECRET_KEY_AUTO_GENERATE": False,
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
    # database（默认与 config_example.yml 模板一致：PostgreSQL + compose 服务名）。
    # 兜底值仅在配置文件未给该键时生效；改用其他引擎（mysql/sqlite3/vastbase）需在
    # config.yml 中显式配置 DB_ENGINE / DB_HOST / DB_PORT 三项。
    "DB_ENGINE": "postgresql",
    "DB_HOST": "postgresql",
    "DB_PORT": 5432,
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
    # 默认队列 worker 并发：与 config_example.yml 保持一致（模板是唯一事实源，
    # 零配置回落路径直接读模板；此处仅作「配置文件未给该键」时的兜底）
    "CELERY_WORKER_COUNT": 4,
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
    # 结构化输出（NL 查数 DSL / 动作草稿 JSON）在未配置 AI_MAX_TOKENS 时的安全上限；
    # 思考型模型思考消耗 token 多，可通过 AI 配置页调大（0 = 回落内置默认 2048）
    "AI_STRUCTURED_MAX_TOKENS": 2048,
    "AI_TOP_P": None,
    "AI_FREQUENCY_PENALTY": None,
    "AI_PRESENCE_PENALTY": None,
    "AI_STOP": "",
    "AI_SEED": None,
    "AI_MAX_RETRIES": 0,
    "AI_CONTEXT_LIMIT": 20,
    "AI_PERSONA": "",
}

LIBS_CONFIG = {
    # REST_FRAMEWORK
    "DEFAULT_THROTTLE_RATES": {},
    # SIMPLE_JWT
    "ACCESS_TOKEN_LIFETIME": 3600,  # Unit: second
    "REFRESH_TOKEN_LIFETIME": 15 * 24 * 3600,  # Unit: second
}
