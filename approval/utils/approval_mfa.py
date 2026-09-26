#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批动作 MFA 二次确认门控。

``APPROVAL_MFA_REQUIRED_ACTIONS``（SysConfig，默认空 = 不启用）命中的动作在业务
变更前走 412（``user_confirm_required``）协议 —— 与「敏感操作审批令牌」是两套独立
协议，本项复用 MFA 的确认状态缓存（前端 http 层已支持弹验证窗并自动重发）。

支持的动作名：``approve`` / ``reject`` / ``cancel`` / ``add_sign`` / ``transfer`` /
``batch_approve`` / ``batch_reject`` / ``rollback``（流程定义回滚）。
覆盖两个入口：审批中心（ApprovalRequest）与审批流引擎（ApprovalInstance / 流程定义）。

跨 app 仅经 mfa 的 services 契约层，且惰性 import（common 侧模块不反向依赖 mfa）。
"""

from common.core.config import SysConfig


def ensure_approval_action_confirmed(request, action: str):
    """命中清单时校验 MFA 确认状态；未确认由 MFA 层抛 412，业务代码不执行。"""
    from mfa.const import ConfirmType
    from mfa.services import ensure_user_confirmed

    if action in (SysConfig.APPROVAL_MFA_REQUIRED_ACTIONS or []):
        ensure_user_confirmed(request, ConfirmType.MFA)
