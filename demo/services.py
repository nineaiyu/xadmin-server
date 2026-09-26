#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""demo 业务与框架能力的挂接示例：上架审批（审批流引擎）与终态回写。

示范三件事（对应文档中心的审批流引擎接入口径）：

1. **提交**：校验业务状态 → 解析启用的流程定义 → ``create_instance``
   （``biz_type`` / ``biz_id`` 绑定业务单）→ 业务单置 PENDING 并挂上实例；
2. **回写**：实例终态经 ``approval_instance_finished`` 信号（``system/signal_handler.py``
   的集中分发器按 ``biz_type`` 分发）回到 :func:`sync_book_instance`，
   把 APPROVED / REJECTED / CANCELLED 同步为业务状态；
3. **组织方式**：跨 app 的引擎调用一律**函数内惰性导入**（跨 app import 门禁合规），
   与 ``system/utils/leave.py`` 的请假业务接入互为对照。
"""

from django.utils import timezone

from common.utils import get_logger

logger = get_logger(__name__)

# 业务标识：ApprovalInstance.biz_type / approval_instance_finished 分发键
BOOK_BIZ_TYPE = "demo_book"
# 流程定义 code：由 seed_demo_book 命令灌入（流程列表可见；停用即拒绝提交）
BOOK_FLOW_CODE = "demo_book"


def _models():
    """延迟导入模型：避免 app 装载期的导入顺序问题。"""
    from demo.models import Book

    return Book


def resolve_book_flow():
    """解析上架审批流程定义（code=demo_book 的启用流程）；缺失返回 None。"""
    from approval.models.approval import ApprovalFlow

    return ApprovalFlow.objects.filter(code=BOOK_FLOW_CODE, is_active=True).first()


def submit_book(book, user):
    """提交上架审批：返回 (ok, detail)。

    仅「草稿 / 已驳回」可提交（审批中在途、已上架不可重提）；无可用流程定义时
    拒绝提交并提示（fail-closed，不静默直通——缺流程是配置问题，不该假装成功）。
    """
    Book = _models()
    if book.status == Book.Status.PENDING:
        return False, "该书籍已在审批中"
    if book.status == Book.Status.ON_SHELF:
        return False, "该书籍已上架"

    flow = resolve_book_flow()
    if flow is None:
        return False, f"未找到启用的上架审批流程（code: {BOOK_FLOW_CODE}），请先执行 seed_demo_book"

    from approval.utils.approval_flow import create_instance

    instance, error = create_instance(
        flow=flow,
        applicant=user,
        title=f"书籍上架：{book.name}",
        # 表单数据随实例快照（流程条件节点 / 字段审批人可按这些 key 取值）
        form_data={
            "name": book.name,
            "isbn": book.isbn,
            "author": book.author,
            "publisher": book.publisher,
            "price": float(book.price or 0),
        },
        biz_type=BOOK_BIZ_TYPE,
        biz_id=str(book.pk),
    )
    if error:
        return False, error

    book.instance = instance
    book.status = Book.Status.PENDING
    book.modifier = user
    book.save(update_fields=["instance", "status", "modifier", "updated_time"])
    return True, "已提交上架审批"


def sync_book_instance(instance, status, reason: str = "") -> None:
    """审批终态回写业务单：由 ``system/signal_handler.py`` 的信号分发器调用（幂等）。

    - APPROVED → 已上架（同时启用 ``is_active``，演示「审批通过产生业务效果」）；
    - REJECTED → 已驳回；CANCELLED → 回到草稿。
    """
    from approval.models.approval import ApprovalInstance

    if getattr(instance, "biz_type", "") != BOOK_BIZ_TYPE or not instance.biz_id:
        return
    Book = _models()
    status = str(status)
    mapping = {
        str(ApprovalInstance.Status.APPROVED): Book.Status.ON_SHELF,
        str(ApprovalInstance.Status.REJECTED): Book.Status.REJECTED,
        str(ApprovalInstance.Status.CANCELLED): Book.Status.DRAFT,
    }
    if status not in mapping:
        return
    book = Book.objects.filter(pk=instance.biz_id).first()
    if book is None:
        logger.warning(
            "demo book sync skipped, business row missing. instance:%s biz_id:%s", instance.pk, instance.biz_id
        )
        return
    target = mapping[status]
    update_fields = ["status", "modifier", "updated_time"]
    book.status = target
    if target == Book.Status.ON_SHELF:
        if not book.is_active:
            # 审批通过顺带启用（演示「审批结果产生业务效果」）
            book.is_active = True
            update_fields.append("is_active")
        if book.on_shelf_time is None:
            book.on_shelf_time = timezone.now()
            update_fields.append("on_shelf_time")
    book.modifier = instance.creator
    book.save(update_fields=update_fields)
    logger.info("demo book status synced by approval instance. book:%s status:%s reason:%s", book.pk, target, reason)
