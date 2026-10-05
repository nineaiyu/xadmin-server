#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""过期审计日志清理实现体（任务函数壳留在 system.tasks，celery 任务名不变）。

先归档后清理：归档（JSONL.gz，含 sha256 与清单）与清理边界均由
``audit/utils/log_archive`` 的水位逻辑驱动。
"""

from celery.utils.log import get_task_logger

from audit.models import OperationLog, UserLoginLog

logger = get_task_logger(__name__)


def auto_clean_operation_log(clean_day=None):
    """先归档后清理过期审计日志：操作日志 + 登录日志。

    - 归档：把「整月已超保留期」的日志导出为 ``JSONL.gz``（含 sha256 与清单，幂等）；
    - 清理：边界由**归档水位**驱动（删必已归档；未归档的边界月最多多留一个月）；
    - 保留期：操作日志 ``OPERATION_LOG_RETENTION_DAYS``（错误日志按
      ``OPERATION_LOG_ERROR_RETENTION_DAYS`` 分层留存）、登录日志
      ``LOGIN_LOG_RETENTION_DAYS``（默认 365）；对象保留期置 0 = 该对象不清理；
    - 归档失败时抛错跳过本次清理（保数据优先），任务在 celery 记录中可见失败，
      避免「未归档即删除」的静默数据损失；
    - 同时输出剩余行数，便于监控告警：清理强依赖 celery beat 部署，
      beat 未部署时该任务静默不执行，表会无限增长。
    """
    from audit.utils.log_archive import archive_expired, prune_archived

    deleted = 0
    for model_key, model in (("operation", OperationLog), ("login", UserLoginLog)):
        # clean_day 参数（历史签名）只覆盖操作日志保留期；登录日志走自身配置
        override = clean_day if model_key == "operation" else None
        archive_expired(model_key, retention_days=override)
        deleted += prune_archived(model_key, retention_days=override)
        logger.info(f"clean {model_key} log remaining {model.objects.count()}")
    return deleted
