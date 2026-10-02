#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
"""系统配置缓存：日志保留 / 导入导出 / 会话与监控域（自 system_conf.py 按域拆出）。

经 system_conf.BaseConfCache 组装；默认值单一来源与键说明见 system_conf.py。
"""

from server.const import CONFIG

from .base import ConfigCacheBase


class OpsConfMixin(ConfigCacheBase):
    """日志保留 / 下载中心 / 会话 / 监控 / 聊天配置。"""

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
        """登录日志保留天数（冷归档自动面，默认 365 天；0 = 不自动清理，仅支持手动归档）。"""
        return self.get_value("LOGIN_LOG_RETENTION_DAYS", CONFIG.LOGIN_LOG_RETENTION_DAYS)

    @property
    def ACCOUNT_EXPIRY_REMIND_DAYS(self):
        """账号到期提醒天数：到期前 N 天站内信 + 邮件提醒；0 = 关闭提醒。"""
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
