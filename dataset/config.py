#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""dataset 域装配声明。

APPROVAL_BIZ_SYNCERS：审批终态回写业务同步器（biz_type → 导入路径），
由 approval/biz_sync.py 注册表按声明收集——新增业务回写零核心文件改动。
"""

# 动态表单提交单：流程实例终态回写提交状态（dataset/utils/dform_flow.py）
APPROVAL_BIZ_SYNCERS = {
    "dform_submission": "dataset.utils.dform_flow.sync_dform_instance",
}
