#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
"""系统配置缓存：安全审批 / SCIM / CSP / OAuth 域（自 system_conf.py 按域拆出）。

经 system_conf.BaseConfCache 组装；默认值单一来源与键说明见 system_conf.py。
"""

from server.const import CONFIG

from .base import ConfigCacheBase


class SecurityConfMixin(ConfigCacheBase):
    """敏感操作 / 审批 / SCIM / CSP / OAuth 配置。"""

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
        权限码的在用用户视为职能审批人（system.services.get_users_by_perm 反查）。
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
    def OAUTH_PROVIDERS(self):
        """第三方登录 provider 列表（JSON 数组，默认空 = 整体休眠）。

        每项结构见 `system/utils/oauth.py`：key/name/enabled/client_id/client_secret/
        authorize_url/token_url/userinfo_url/scope/subject_field/auto_create。
        密钥仅服务端可见，列表接口回传时掩码（见 `mask_providers`）。
        """
        return self.get_value("OAUTH_PROVIDERS", CONFIG.OAUTH_PROVIDERS)

    @property
    def OUTBOUND_ALLOWED_HOSTS(self):
        """出站请求域名/IP 白名单（逗号分隔，默认空 = 不启用）。

        白名单是私网目标的唯一放行途径：Webhook 等出站请求默认拒绝私网/环回/
        link-local 地址（防 SSRF）；内网自建接收端在此登记后放行（元数据地址仍拒）。
        """
        return self.get_value("OUTBOUND_ALLOWED_HOSTS", CONFIG.OUTBOUND_ALLOWED_HOSTS)

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
