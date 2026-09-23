#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : config
# author : ly_13
# date : 12/15/2023
"""系统配置缓存：系统级配置属性与渲染上下文。"""

from common.utils import get_logger
from server.const import CONFIG

from .base import ConfigCacheBase

logger = get_logger(__name__)


class BaseConfCache(ConfigCacheBase):
    """系统级配置读取（键 → 值）。

    默认值单一来源：全部回读 ``server/conf.py`` 的静态配置实例 ``CONFIG``
    （即 config.yml / 环境变量的值或代码默认值），本类不再硬编码任何默认值；
    ``loadjson/systemconfig.json`` 的种子初值须与 conf.py 一致（守护测试校验）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @property
    def FILE_UPLOAD_SIZE(self):
        return self.get_value("FILE_UPLOAD_SIZE", CONFIG.FILE_UPLOAD_SIZE)

    @property
    def PICTURE_UPLOAD_SIZE(self):
        return self.get_value("PICTURE_UPLOAD_SIZE", CONFIG.PICTURE_UPLOAD_SIZE)

    @property
    def OPERATION_LOG_RETENTION_DAYS(self):
        """操作日志保留天数（清理保留期配置化，默认 180 天）"""
        return self.get_value("OPERATION_LOG_RETENTION_DAYS", CONFIG.OPERATION_LOG_RETENTION_DAYS)

    @property
    def SEARCH_CHOICES_MAX_COUNT(self):
        """search-columns / search-fields 关联列 choices 的最大返回条数（默认 200）。

        大表关联字段请务必自定义 input_type='api-search-*'（远程搜索），否则超出的
        选项不会出现在下拉里，且接口会带出 choices_truncated 标记。
        """
        return self.get_value("SEARCH_CHOICES_MAX_COUNT", CONFIG.SEARCH_CHOICES_MAX_COUNT)

    @property
    def SLOW_REQUEST_THRESHOLD(self):
        """慢请求阈值（秒）：超阈值打 warning 日志，监控面板 slow 接口同口径（默认 1.0）。"""
        return float(self.get_value("SLOW_REQUEST_THRESHOLD", CONFIG.SLOW_REQUEST_THRESHOLD))

    @property
    def OPERATION_LOG_ERROR_RETENTION_DAYS(self):
        """错误操作日志（status_code != 1000）额外保留天数（默认 365；0/空 = 跟随全量保留期）。"""
        return self.get_value("OPERATION_LOG_ERROR_RETENTION_DAYS", CONFIG.OPERATION_LOG_ERROR_RETENTION_DAYS)

    @property
    def LOGIN_LOG_RETENTION_DAYS(self):
        """登录日志保留天数（P-5 冷归档自动面，默认 365 天；0 = 不自动清理，仅支持手动归档）。"""
        return self.get_value("LOGIN_LOG_RETENTION_DAYS", CONFIG.LOGIN_LOG_RETENTION_DAYS)

    @property
    def ACCOUNT_EXPIRY_REMIND_DAYS(self):
        """账号到期提醒天数（F-11）：到期前 N 天站内信 + 邮件提醒；0 = 关闭提醒。"""
        return self.get_value("ACCOUNT_EXPIRY_REMIND_DAYS", CONFIG.ACCOUNT_EXPIRY_REMIND_DAYS)

    @property
    def WEB_SITE_URL(self):
        """站点对外访问地址（邀请 / 通知邮件链接基址；空 = 按请求推导）。"""
        return self.get_value("WEB_SITE_URL", CONFIG.WEB_SITE_URL)

    @property
    def OPERATION_LOG_FIELD_MAX(self):
        """操作日志大字段（请求体/响应/变更 diff）截断上限（字符，默认 4096；0 = 不落大字段内容）。"""
        return self.get_value("OPERATION_LOG_FIELD_MAX", CONFIG.OPERATION_LOG_FIELD_MAX)

    @property
    def SENSITIVE_OPERATION_METHODS(self):
        """敏感操作告警的 HTTP 方法清单（默认 ["DELETE"]；"ALL" 或空表示不按方法过滤）。"""
        return self.get_value("SENSITIVE_OPERATION_METHODS", CONFIG.SENSITIVE_OPERATION_METHODS)

    @property
    def SENSITIVE_OPERATION_PATHS(self):
        """敏感操作告警的路径正则清单（默认空 = 不按路径过滤，与方法清单 AND 组合）。"""
        return self.get_value("SENSITIVE_OPERATION_PATHS", CONFIG.SENSITIVE_OPERATION_PATHS)

    @property
    def APPROVAL_REQUIRED_PATHS(self):
        """敏感操作审批拦截的路径正则清单（默认空 = 审批整体休眠，渐进启用）。

        仅对显式挂载 ApprovalRequired 装饰器的 action 生效；命中清单的请求需先
        经审批中心通过后携令牌重发（一次性通行令牌）。
        """
        return self.get_value("APPROVAL_REQUIRED_PATHS", CONFIG.APPROVAL_REQUIRED_PATHS)

    @property
    def APPROVAL_MFA_REQUIRED_ACTIONS(self):
        """需 MFA 二次确认的审批动作清单（默认空 = 不启用；审批流三期）。

        取值 approve / reject / cancel / add_sign / batch_approve / batch_reject / rollback；
        覆盖审批中心与审批流引擎（含流程定义回滚）两个入口，命中动作在业务变更前走
        412（user_confirm_required）协议，前端弹验证窗后自动重发。
        """
        return self.get_value("APPROVAL_MFA_REQUIRED_ACTIONS", CONFIG.APPROVAL_MFA_REQUIRED_ACTIONS)

    @property
    def APPROVAL_APPROVER_ROLES(self):
        """审批人角色 code 清单（默认空 = 全部在用超管；申请人始终不能自审）。"""
        return self.get_value("APPROVAL_APPROVER_ROLES", CONFIG.APPROVAL_APPROVER_ROLES)

    @property
    def APPROVAL_APPROVER_PERMS(self):
        """审批人职能权限码清单（"动作:组件名"，默认空 = 不按权限反查）。

        与 APPROVAL_APPROVER_ROLES 取并集，两者皆空回退全部在用超管；持有任一
        权限码的在用用户视为职能审批人（system.services.get_users_by_perm 反查，
        借鉴 jumpserver 按权限反查角色的审批人推导模式）。
        """
        return self.get_value("APPROVAL_APPROVER_PERMS", CONFIG.APPROVAL_APPROVER_PERMS)

    @property
    def APPROVAL_TOKEN_TTL(self):
        """审批通过后令牌有效期（秒，默认 300）：有效期内携令牌重发一次有效。"""
        return int(self.get_value("APPROVAL_TOKEN_TTL", CONFIG.APPROVAL_TOKEN_TTL))

    @property
    def APPROVAL_PENDING_TIMEOUT(self):
        """待审批单超时天数（默认 3 天）：超时由清理任务置 EXPIRED。"""
        return int(self.get_value("APPROVAL_PENDING_TIMEOUT", CONFIG.APPROVAL_PENDING_TIMEOUT))

    @property
    def APPROVAL_KEEP_DAYS(self):
        """审批单保留天数（默认 180）：超过由清理任务分批删除。"""
        return int(self.get_value("APPROVAL_KEEP_DAYS", CONFIG.APPROVAL_KEEP_DAYS))

    @property
    def APPROVAL_REMIND_HOURS(self):
        """待审批超时提醒阈值（小时，默认 24；0 = 不提醒）。

        由每日提醒任务对「PENDING 且已超过该时长且未提醒过」的单向审批人补发一次提醒。
        """
        return int(self.get_value("APPROVAL_REMIND_HOURS", CONFIG.APPROVAL_REMIND_HOURS))

    @property
    def APPROVAL_FLOW_KEEP_DAYS(self):
        """流程实例保留天数（默认 365）：超过由清理任务分批删除（级联节点任务）。"""
        return int(self.get_value("APPROVAL_FLOW_KEEP_DAYS", CONFIG.APPROVAL_FLOW_KEEP_DAYS))

    @property
    def LEAVE_APPROVAL_FLOW_CODE(self):
        """请假审批流程 code（默认 leave）：请假单提交时绑定的流程定义。

        该 code 的流程不存在或未启用时，按「leave_<请假类型>」再回退「leave 前缀的
        启用流程」查找（见 system/utils/leave.py:resolve_leave_flow），全找不到则拒绝
        提交并提示管理员配置流程。
        """
        return self.get_value("LEAVE_APPROVAL_FLOW_CODE", CONFIG.LEAVE_APPROVAL_FLOW_CODE)

    @property
    def FILE_OFFICE_PREVIEW_ENABLED(self):
        """Office 在线预览开关（默认开）：关闭或未装 LibreOffice 时按不支持降级。"""
        return self.get_value("FILE_OFFICE_PREVIEW_ENABLED", CONFIG.FILE_OFFICE_PREVIEW_ENABLED)

    @property
    def FILE_OFFICE_MAX_BYTES(self):
        """Office 预览转换大小上限（字节，默认 20MB）：超过不转换（保护转换进程）。"""
        return int(self.get_value("FILE_OFFICE_MAX_BYTES", CONFIG.FILE_OFFICE_MAX_BYTES))

    @property
    def FILE_OFFICE_CONVERT_TIMEOUT(self):
        """LibreOffice 单次转换超时（秒，默认 60）：超时杀进程并降级为不可预览。"""
        return int(self.get_value("FILE_OFFICE_CONVERT_TIMEOUT", CONFIG.FILE_OFFICE_CONVERT_TIMEOUT))

    @property
    def FILE_OFFICE_WAIT_SECONDS(self):
        """请求侧等待转换产物的窗口（秒，默认 8）：未等到返回 1006 由前端重试。"""
        return int(self.get_value("FILE_OFFICE_WAIT_SECONDS", CONFIG.FILE_OFFICE_WAIT_SECONDS))

    @property
    def FILE_OFFICE_SOFFICE_BIN(self):
        """LibreOffice 可执行文件路径（默认空 = 自动探测 PATH 与常见安装路径）。"""
        return self.get_value("FILE_OFFICE_SOFFICE_BIN", CONFIG.FILE_OFFICE_SOFFICE_BIN)

    @property
    def SCIM_ENABLED(self):
        """SCIM 用户目录同步总开关（默认关，S1）：开启且配置 SCIM_TOKEN 后生效。"""
        return self.get_value("SCIM_ENABLED", CONFIG.SCIM_ENABLED)

    @property
    def SCIM_TOKEN(self):
        """SCIM 独立 Bearer Token（默认空 = 未配置，任何请求 401；access=false 不对外回传）。"""
        return self.get_value("SCIM_TOKEN", CONFIG.SCIM_TOKEN)

    @property
    def SCIM_RATE_LIMIT(self):
        """SCIM 凭证级限流速率（SimpleRateThrottle 速率串，默认 600/min；空或 0 = 不限）。"""
        return self.get_value("SCIM_RATE_LIMIT", CONFIG.SCIM_RATE_LIMIT)

    @property
    def SCIM_DEFAULT_ROLE_CODE(self):
        """SCIM 新建用户的默认角色 code（默认空 = 不分配角色，由 IdP 分组另行下发）。"""
        return self.get_value("SCIM_DEFAULT_ROLE_CODE", CONFIG.SCIM_DEFAULT_ROLE_CODE)

    @property
    def BACKUP_ALERT_TOKEN(self):
        """备份失败告警回调令牌（默认空 = 端点未启用；access=false 不对外回传）。"""
        return self.get_value("BACKUP_ALERT_TOKEN", CONFIG.BACKUP_ALERT_TOKEN)

    @property
    def OPS_ALERT_TOKEN(self):
        """运维告警回调令牌（默认空 = 端点未启用；access=false 不对外回传）。"""
        return self.get_value("OPS_ALERT_TOKEN", CONFIG.OPS_ALERT_TOKEN)

    @property
    def CSP_MODE(self):
        """CSP 模式（S3，默认 report-only 观察期）：disabled / report-only / enforce。"""
        return self.get_value("CSP_MODE", CONFIG.CSP_MODE)

    @property
    def CSP_REPORT_URI(self):
        """CSP 违规上报地址（默认空 = 不下发 report-uri）：一般指向 /api/common/api/csp-report。"""
        return self.get_value("CSP_REPORT_URI", CONFIG.CSP_REPORT_URI)

    @property
    def PAT_RATE_LIMIT(self):
        """PAT 凭证级限流速率（SimpleRateThrottle 速率串，默认 60/min；空或 0 = 不限）。"""
        return self.get_value("PAT_RATE_LIMIT", CONFIG.PAT_RATE_LIMIT)

    @property
    def FILE_STORAGE_QUOTA_MB(self):
        """个人文件存储配额（MB，默认 0 = 不限）：上传前按 creator 聚合校验。"""
        return int(self.get_value("FILE_STORAGE_QUOTA_MB", CONFIG.FILE_STORAGE_QUOTA_MB))

    @property
    def FILE_KEEP_DAYS(self):
        """正式上传文件保留天数（默认 0 = 不清理）。

        仅清理「非临时、无业务引用」的历史文件；物理文件删除由磁盘引用守护兜底。
        """
        return int(self.get_value("FILE_KEEP_DAYS", CONFIG.FILE_KEEP_DAYS))

    @property
    def FILE_UPLOAD_COUNT_LIMIT(self):
        """个人上传文件数量上限（默认 0 = 不限）：上传前按 creator 计数校验。"""
        return int(self.get_value("FILE_UPLOAD_COUNT_LIMIT", CONFIG.FILE_UPLOAD_COUNT_LIMIT))

    @property
    def FILE_PREVIEW_TEXT_MAX_BYTES(self):
        """文本预览读取上限（字节，默认 256KB）：超过即截断并提示下载查看。

        避免把一个几百 MB 的日志整份读进内存再回给浏览器。
        """
        return int(self.get_value("FILE_PREVIEW_TEXT_MAX_BYTES", CONFIG.FILE_PREVIEW_TEXT_MAX_BYTES))

    @property
    def FILE_PREVIEW_THUMB_WIDTH(self):
        """列表缩略图宽度（像素，默认 240）：按原图比例等比缩放，不拉伸。"""
        return int(self.get_value("FILE_PREVIEW_THUMB_WIDTH", CONFIG.FILE_PREVIEW_THUMB_WIDTH))

    @property
    def FILE_PREVIEW_IMAGE_WIDTH(self):
        """抽屉大图宽度（像素，默认 1280）：原图更小时不放大。"""
        return int(self.get_value("FILE_PREVIEW_IMAGE_WIDTH", CONFIG.FILE_PREVIEW_IMAGE_WIDTH))

    @property
    def OAUTH_PROVIDERS(self):
        """第三方登录 provider 列表（JSON 数组，默认空 = 整体休眠）。

        每项结构见 `system/utils/oauth.py`：key/name/enabled/client_id/client_secret/
        authorize_url/token_url/userinfo_url/scope/subject_field/auto_create。
        密钥仅服务端可见，列表接口回传时掩码（见 `mask_providers`）。
        """
        return self.get_value("OAUTH_PROVIDERS", CONFIG.OAUTH_PROVIDERS)

    @property
    def FILE_PREVIEW_CACHE_KEEP_DAYS(self):
        """预览缓存保留天数（默认 7）：缓存是派生产物，过期删除后按需重建。"""
        return int(self.get_value("FILE_PREVIEW_CACHE_KEEP_DAYS", CONFIG.FILE_PREVIEW_CACHE_KEEP_DAYS))

    @property
    def FILE_STORAGE_BACKEND(self):
        """文件存储后端（默认 local = 本地磁盘）：local / s3 / mirror（搬迁窗口双写，声明式可插拔）。"""
        return self.get_value("FILE_STORAGE_BACKEND", CONFIG.FILE_STORAGE_BACKEND)

    @property
    def FILE_S3_ENDPOINT(self):
        """S3 兼容对象存储端点（默认空 = 按区域使用默认端点，如 AWS）。"""
        return self.get_value("FILE_S3_ENDPOINT", CONFIG.FILE_S3_ENDPOINT)

    @property
    def FILE_S3_BUCKET(self):
        """对象存储桶名（backend=s3 时必填，缺省回退本地）。"""
        return self.get_value("FILE_S3_BUCKET", CONFIG.FILE_S3_BUCKET)

    @property
    def FILE_S3_ACCESS_KEY(self):
        """对象存储 access key（敏感值，落库经 signer 加密）。"""
        return self.get_value("FILE_S3_ACCESS_KEY", CONFIG.FILE_S3_ACCESS_KEY)

    @property
    def FILE_S3_SECRET_KEY(self):
        """对象存储 secret key（敏感值，落库经 signer 加密）。"""
        return self.get_value("FILE_S3_SECRET_KEY", CONFIG.FILE_S3_SECRET_KEY)

    @property
    def FILE_S3_REGION(self):
        """对象存储区域（默认空 = 由 SDK / 端点决定）。"""
        return self.get_value("FILE_S3_REGION", CONFIG.FILE_S3_REGION)

    @property
    def FILE_S3_CUSTOM_DOMAIN(self):
        """对象存储访问域名（CDN / 公开桶；非空时文件 URL 不签名）。"""
        return self.get_value("FILE_S3_CUSTOM_DOMAIN", CONFIG.FILE_S3_CUSTOM_DOMAIN)

    @property
    def FILE_S3_ADDRESSING_STYLE(self):
        """S3 寻址风格（path / virtual；空 = 由 SDK 决定，MinIO 常需 path）。"""
        return self.get_value("FILE_S3_ADDRESSING_STYLE", CONFIG.FILE_S3_ADDRESSING_STYLE)

    @property
    def AUDIT_DIFF_MODELS(self):
        """字段级审计 diff 白名单（模型 _meta.label JSON 清单，默认空 = 关闭）。

        优先读系统配置（管理员可运行时扩容），未登记时回退 django settings
        （config.yml 链路 / settings_e2e.py 的 AUDIT_DIFF_MODELS 仍然生效）。
        注意必须用 django.conf.settings 惰性对象：静态 conf 链（CONFIG）读不到
        settings_e2e 尾部的显式覆盖；本键是唯一需要与测试覆盖联动的例外，
        SysConfig 其余键的默认值统一单源在 server/conf.py（见 BaseConfCache 说明）。
        """
        from django.conf import settings as dj_settings

        return self.get_value("AUDIT_DIFF_MODELS", getattr(dj_settings, "AUDIT_DIFF_MODELS", []) or [])

    @property
    def EXPORT_FILE_KEEP_DAYS(self):
        """异步导出记录与产物保留天数（下载中心，默认 7 天）。"""
        return int(self.get_value("EXPORT_FILE_KEEP_DAYS", CONFIG.EXPORT_FILE_KEEP_DAYS))

    @property
    def EXPORT_ASYNC_MAX_RUNNING(self):
        """同一用户同时进行中的异步导出任务上限（默认 3；0 表示不限制）。"""
        return int(self.get_value("EXPORT_ASYNC_MAX_RUNNING", CONFIG.EXPORT_ASYNC_MAX_RUNNING))

    @property
    def MONITOR_RETENTION_DAYS(self):
        """主机监控心跳历史保留天数（common.Monitor 30s 一条，默认 30 天）。"""
        return int(self.get_value("MONITOR_RETENTION_DAYS", CONFIG.MONITOR_RETENTION_DAYS))

    @property
    def SESSION_ONLINE_TIMEOUT(self):
        """纯 HTTP 会话的在线判定窗口（秒）：last_active 超过该窗口视为离线（默认 300）。"""
        return int(self.get_value("SESSION_ONLINE_TIMEOUT", CONFIG.SESSION_ONLINE_TIMEOUT))

    @property
    def USER_SESSION_RETENTION_DAYS(self):
        """已结束会话记录保留天数（在线用户/会话管理，默认 30 天）。"""
        return int(self.get_value("USER_SESSION_RETENTION_DAYS", CONFIG.USER_SESSION_RETENTION_DAYS))

    @property
    def IMPORT_RECORD_KEEP_DAYS(self):
        """异步导入记录、源文件与错误报告保留天数（下载中心，默认 30 天）。"""
        return int(self.get_value("IMPORT_RECORD_KEEP_DAYS", CONFIG.IMPORT_RECORD_KEEP_DAYS))

    @property
    def CHAT_HISTORY_DAYS(self):
        """聊天消息保留天数（二期，默认 0 = 不清理）。

        超过保留期的消息由每日清理任务分批删除；会话与成员关系保留，
        历史清空的会话在列表里仅摘要为空。
        """
        return int(self.get_value("CHAT_HISTORY_DAYS", CONFIG.CHAT_HISTORY_DAYS))

    @property
    def IMPORT_FAIL_RATE_LIMIT(self):
        """异步导入失败率中止阈值（默认 0.5；0 表示不按失败率中止）。"""
        return float(self.get_value("IMPORT_FAIL_RATE_LIMIT", CONFIG.IMPORT_FAIL_RATE_LIMIT))

    @property
    def IMPORT_ASYNC_MAX_RUNNING(self):
        """同一用户同时进行中的异步导入任务上限（默认 3；0 表示不限制）。"""
        return int(self.get_value("IMPORT_ASYNC_MAX_RUNNING", CONFIG.IMPORT_ASYNC_MAX_RUNNING))

    @property
    def IMPORT_VALIDATE_ERROR_LIMIT(self):
        """导入前校验返回的错误行明细上限（默认 200，超出截断并标记）。"""
        return int(self.get_value("IMPORT_VALIDATE_ERROR_LIMIT", CONFIG.IMPORT_VALIDATE_ERROR_LIMIT))


class MessagePushConfCache(ConfigCacheBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @property
    def PUSH_MESSAGE_NOTICE(self):
        return self.get_value("PUSH_MESSAGE_NOTICE", CONFIG.PUSH_MESSAGE_NOTICE)

    @property
    def PUSH_CHAT_MESSAGE(self):
        return self.get_value("PUSH_CHAT_MESSAGE", CONFIG.PUSH_CHAT_MESSAGE)


class ConfigCache(BaseConfCache, MessagePushConfCache):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


SysConfig = ConfigCache()
