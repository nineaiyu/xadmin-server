# -*- coding: utf-8 -*-
"""周期任务归属守护：可裁剪模块的任务必须声明 module= 且 id 真实存在。

模块裁剪的任务层切片依赖 ``@register_as_period_task(module=...)`` 声明：
停用模块的周期任务不注册、历史条目启动期清理（common.tasks.create_or_update_
registered_periodic_tasks）。漏声明 = 模块停用后任务仍照常调度（「路由已 404、
后台仍空转」的半残状态）；写错 id = 停用该模块时任务不会消失。
"""

import pytest

from common.celery.decorator import get_register_period_tasks
from common.core.modules import module_index

# module=None 的内核周期任务白名单：对应内核模块（core_*，不可裁剪），随系统常驻。
# 新增内核任务在此登记；属于可裁剪模块（可选/标配，见 common/core/modules/catalog.py）
# 的任务必须声明 module=<模块 id>，不得加入本清单。
CORE_PERIODIC_TASKS = frozenset(
    {
        # 审计日志保留期清理（core_log）
        "system.tasks.auto_clean_operation_job",
        # 登录黑名单/令牌黑名单清理（core_auth）
        "system.tasks.auto_clean_black_token_job",
        # 账号有效期维护（core_auth）
        "system.tasks.account_expiry_job",
        # 临时文件/正式上传文件/Office 预览缓存清理（core_file）
        "system.tasks.auto_clean_tmp_file_job",
        "system.tasks.auto_clean_upload_file_job",
        "system.tasks.auto_clean_preview_cache_job",
        "system.tasks.auto_clean_upload_sessions_job",
        "system.tasks.auto_clean_file_access_log_job",
        # 任务执行历史/异步导出记录/异步导入记录清理（下载中心，core_file）
        "system.tasks.auto_clean_task_execution_job",
        "system.tasks.auto_clean_export_record_job",
        "system.tasks.auto_clean_import_record_job",
        # HTTP 会话活跃窗口/会话记录保留期（core_auth）
        "system.tasks.auto_expire_user_session_job",
        "system.tasks.auto_clean_user_session_job",
        # 个人访问令牌清理（core_open_credential）
        "system.tasks.auto_clean_pat_job",
        # 账号安全风险巡检（core_rbac）
        "system.tasks.scan_account_risk_job",
        # 公开演示账号自愈：无演示数据时零成本跳过（内核常驻，非 demo 模块开关）
        "system.tasks.demo_account_selfheal_job",
        # 验证码存储清理 / 观测日志保留期清理（core_auth）
        "captcha.tasks.auto_clean_expired_captcha_job",
        "captcha.tasks.purge_captcha_logs_job",
        # 回收站软删数据物理清除（core_rbac）
        "common.tasks.purge_soft_deleted",
    }
)


def _registered_entries() -> list[tuple[str, object]]:
    """全量周期任务注册条目 [(任务路径, module 声明), ...]，先强制 autodiscover 补齐。"""

    from task.services.periodic import ensure_tasks_registered

    ensure_tasks_registered(force=True)
    return [
        (detail["task"], detail.get("module"))
        for entry in get_register_period_tasks()
        for _name, detail in entry.items()
    ]


class TestPeriodicTaskModuleOwnership:
    def test_registry_is_not_empty(self):
        # 守护前提：autodiscover 后注册表非空（否则下面的断言全部空转）
        assert len(_registered_entries()) >= 20

    def test_module_ids_reference_real_modules(self):
        index = module_index()
        unknown = [(task, module) for task, module in _registered_entries() if module and module not in index]
        assert not unknown, f"module= 写了不存在的模块 id：{unknown}"

    def test_undeclared_tasks_must_be_core_allowlisted(self):
        undeclared = [task for task, module in _registered_entries() if not module and task not in CORE_PERIODIC_TASKS]
        assert not undeclared, (
            f"周期任务未声明归属：{undeclared}——属于可裁剪模块的补 module=<模块 id>，"
            "属于内核的登记进本测试的 CORE_PERIODIC_TASKS 并注明所属内核模块"
        )

    def test_ai_usage_cleanup_belongs_to_ai_module(self):
        # 本批补上的归属锚点：AI 用量账本清理随 ai 模块停用而停（防回归）
        entries = dict(_registered_entries())
        assert entries.get("ai.tasks.auto_clean_ai_usage_job") == "ai"

    @pytest.mark.django_db
    def test_disabled_ai_module_task_not_registered(self, module_config, monkeypatch):
        from django_celery_beat.models import PeriodicTask

        from common import tasks as common_tasks
        from task.services.periodic import ensure_tasks_registered

        module_config(disable=["ai"])
        ensure_tasks_registered()
        common_tasks.create_or_update_registered_periodic_tasks()
        assert not PeriodicTask.objects.filter(task="ai.tasks.auto_clean_ai_usage_job").exists()
