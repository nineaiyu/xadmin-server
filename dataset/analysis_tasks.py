#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""定时报表任务：调度分发 + 执行渲染 + 多渠道路送达（邮件 + IM）。

- 分发器每小时跑一次（crontab "5 * * * *"）派发三档报表、每分钟一次派发 cron 报表；
  到期判定 = 「最近一次应当执行的时刻」晚于「上次执行时刻（首次执行前取建单时刻）」，
  因此 beat 延迟 / worker 停机 / 任务积压时会在恢复后补跑漏掉的期次；执行与分发解耦
  （长渲染不阻塞扫描）；
- 执行以**创建者**权限上下文运行数据集（menu 上下文为空 ⇒ 仅未绑菜单的全局
  授权生效，fail-closed 语义不变）；
- 产物复用下载中心：派发方预创建 ExportRecord（pk == celery task_id 契约），
  xlsx 渲染后经 task 域统一导出服务落 UploadFile 并挂 record（MIME/进度/取消
  协议与异步导出同源，见 task.services._export）；
- 投递按 `notify_channels` 逐渠道独立执行（空 = 仅邮件）：邮件携带附件，IM 为
  文本消息（报表名/行数/下载中心提示，收件人取 `im_recipients` 用户主键并按各
  渠道 OAuth 绑定可达性过滤）；任一渠道失败仅记 error 与交付状态，不回滚产物。

大屏数据推送（``push_screen_data``）：beat 每 15s 扫描大屏，仅向「有在线
展示端且距上次推送超过其 refresh 周期（钳 10s）」的屏投递无载荷触发事件
（``screen.data_trigger``）；真正的数据聚合在各展示连接内以浏览者自身权限执行
（dataset/screen_data.py），服务端不做跨用户广播数据。
"""

from datetime import datetime, timedelta

from asgiref.sync import async_to_sync
from celery import shared_task
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.celery.decorator import register_as_period_task
from common.utils import get_logger
from dataset.report_render import (  # noqa: F401  (渲染/投递拆至 report_render，此处再导出保持调用面)
    _deliver_email,
    _deliver_im,
    _excel_safe,
    _render_workbook,
    report_notify_channels,
)

logger = get_logger(__name__)


def report_due(report, now=None) -> bool:
    """到期判定：cron_expression 优先；否则 frequency × send_time(× weekday)。now 仅供测试注入。

    判定口径是「最近一次应当执行的时刻」晚于「上次执行时刻（首次执行前取建单时刻）」，
    而不是「当前分钟正好等于 send_time」——后者在 beat 延迟 / worker 停机 / 任务积压时
    错过命中分钟就整期漏发（daily 要再等一天），且要求分钟必须精确相等。
    """
    now = now or timezone.localtime()
    due_at = last_due_at(report, now)
    if due_at is None:
        return False
    reference = getattr(report, "last_run_at", None) or getattr(report, "created_time", None)
    if reference is None:
        # 无参考点（未落库的裸对象）：视为到期，交由调用方判定
        return True
    return reference < due_at


def last_due_at(report, now=None):
    """最近一次「应当执行」的时刻（无则 None）：cron 取上一个命中时刻，三档取到期点。"""
    now = now or timezone.localtime()
    expression = (getattr(report, "cron_expression", "") or "").strip()
    if expression:
        return _last_cron_hit(expression, now)
    return _three_tier_due_at(report, now)


def _last_cron_hit(expression: str, now=None):
    """cron 表达式在 now 之前（含当分钟）的最近命中时刻：非法表达式返回 None（fail-closed）。"""
    from croniter import croniter

    expression = (expression or "").strip()
    if not croniter.is_valid(expression):
        return None
    now = now or timezone.localtime()
    moment = now.replace(second=0, microsecond=0)
    if croniter.match(expression, moment):
        return moment
    return croniter(expression, moment).get_prev(datetime)


def _parse_send_time(value: str):
    """HH:MM → (hour, minute)：格式异常返回 None（fail-closed，不误发）。"""
    try:
        raw_hour, raw_minute = str(value or "").split(":", 1)
        hour, minute = int(raw_hour), int(raw_minute)
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _three_tier_due_at(report, now=None):
    """daily / weekly / monthly 的最近一次到期时刻（send_time 非法返回 None）。"""
    now = now or timezone.localtime()
    parsed = _parse_send_time(getattr(report, "send_time", ""))
    if parsed is None:
        return None
    now = now.replace(second=0, microsecond=0)
    today_at = now.replace(hour=parsed[0], minute=parsed[1])
    frequency = str(getattr(report, "frequency", "") or "")
    if frequency == "daily":
        return today_at if today_at <= now else today_at - timedelta(days=1)
    if frequency == "weekly":
        weekday = int(getattr(report, "weekday", 0) or 0)
        candidate = today_at - timedelta(days=(now.weekday() - weekday) % 7)
        if candidate > now:
            candidate -= timedelta(days=7)
        return candidate
    if frequency == "monthly":
        # month_day（1~28，序列化器保证范围）：取「最近一次已过的每月 month_day 时刻」；
        # 钳到 28 后任意月份的 replace(day=month_day) 都合法，无月末歧义
        month_day = min(max(int(getattr(report, "month_day", 1) or 1), 1), 28)
        first_of_month = today_at.replace(day=1)
        candidate = first_of_month.replace(day=month_day)
        if candidate <= now:
            return candidate
        return (first_of_month - timedelta(days=1)).replace(day=month_day)
    return None


def _precreate_record(report) -> str:
    """预创建 ExportRecord（下载中心条目），pk 即派发的 celery task_id。

    params 记 ``report_id``：任务中心「重跑」按记录即可重放同一报表。
    """
    from task.services import ExportRecord

    record = ExportRecord.objects.create(
        name=f"{report.name}-{timezone.localtime():%Y%m%d%H%M%S}",
        module="Report",
        file_format="xlsx",
        params={"report_id": str(report.pk)},
        creator=report.creator,
    )
    return str(record.pk)


@shared_task
@register_as_period_task(crontab="5 * * * *", description="定时报表调度分发", module="analysis")
def dispatch_scheduled_reports():
    """每小时扫描 active 报表并派发到期的执行任务（三档频次；cron 报表由每分钟任务负责）。"""
    from dataset.models.dataset import Report

    dispatched = 0
    for report in Report.objects.filter(is_active=True, cron_expression="").iterator():
        try:
            if not report_due(report):
                continue
        except Exception:  # noqa: BLE001 单条判定异常不中断扫描
            logger.warning("report schedule check failed: %s", report.pk, exc_info=True)
            continue
        run_scheduled_report.apply_async(kwargs={"report_id": str(report.pk)}, task_id=_precreate_record(report))
        dispatched += 1
    if dispatched:
        logger.info("dispatched %s scheduled report(s)", dispatched)
    return dispatched


@shared_task
@register_as_period_task(crontab="* * * * *", description="定时报表 cron 表达式调度分发", module="analysis")
def dispatch_cron_reports():
    """每分钟扫描带 cron 表达式的 active 报表并派发（三档报表由每小时任务负责，职责互斥）。"""
    from dataset.models.dataset import Report

    dispatched = 0
    for report in Report.objects.filter(is_active=True).exclude(cron_expression="").iterator():
        try:
            if not report_due(report):
                continue
        except Exception:  # noqa: BLE001 单条判定异常不中断扫描
            logger.warning("report cron check failed: %s", report.pk, exc_info=True)
            continue
        run_scheduled_report.apply_async(kwargs={"report_id": str(report.pk)}, task_id=_precreate_record(report))
        dispatched += 1
    if dispatched:
        logger.info("dispatched %s cron scheduled report(s)", dispatched)
    return dispatched


@shared_task(bind=True)
def run_scheduled_report(self, report_id: str, bookkeep_schedule: bool = True):
    """执行单个报表：数据集渲染 xlsx → ExportRecord（下载中心）→ 邮件附件。

    task_id == 预创建 ExportRecord.pk（CeleryTaskRecordModel 契约）。产物落库、
    进度与取消协议与异步导出同源（task.services 统一导出服务）。

    ``bookkeep_schedule``：调度簿记开关。last_run_at 是到期判定的「上次执行时刻」
    （见 report_due），只有调度派发链路（缺省 True）才推进它；手动运行（run 动作 /
    任务中心重跑传 False）恰好落在到期点与派发扫描之间时，若推进 last_run_at 会
    吞掉当期投递——last_status 照常写入，用户仍能看到本次结果。
    """
    from dataset.models.dataset import Report
    from task.models.task import TaskExecution
    from task.services import KIND_REPORT, ExportRecord, persist_export_artifact, update_progress
    from task.utils.task_center import TaskCancelled, mark_execution_revoked

    record = ExportRecord.objects.filter(pk=self.request.id).first()
    report = Report.objects.filter(pk=report_id).select_related("dataset", "creator").first()
    if record is None or report is None:
        logger.warning("scheduled report record/report missing: %s", report_id)
        return 0
    # 真实投递由 after_task_publish 自动记账；同步执行（apply/EAGER）不发该信号，此处补齐，
    # 保证执行历史页与增量日志在两种环境下都可用（与异步导出/导入链同口径）
    TaskExecution.objects.get_or_create(
        pk=record.pk,
        defaults={"name": "dataset.analysis_tasks.run_scheduled_report", "kwargs": {"report_id": str(report.pk)}},
    )

    record.status = ExportRecord.Status.RUNNING
    record.save(update_fields=["status", "updated_time"])
    user = report.creator

    def bookkeep(status: str) -> None:
        """last_status 必写（用户可见的最近一次结果）；last_run_at 仅调度链路推进。"""
        update_fields = ["last_status", "updated_time"]
        if bookkeep_schedule:
            report.last_run_at = timezone.now()
            update_fields.append("last_run_at")
        report.last_status = status
        report.save(update_fields=update_fields)

    try:
        # 统一进度：报表此前只有终态 100，此处补中间里程碑（查询 → 渲染 → 落盘）；
        # 里程碑即协作式取消安全点，取消检查须在 try 内由 TaskCancelled 分支收敛
        update_progress(KIND_REPORT, record.pk, 20, stage=_("Querying dataset"))
        content, rows = _render_workbook(report, user)
        update_progress(KIND_REPORT, record.pk, 80, stage=_("Rendering workbook"))
        filename = f"{report.name}-{timezone.localtime():%Y%m%d%H%M}.xlsx"
        persist_export_artifact(record, filename, content, user=user)
        record.rows = rows
        record.status = ExportRecord.Status.SUCCESS
        record.progress = 100
        record.save(update_fields=["file", "rows", "status", "progress", "updated_time"])

        errors = []
        channels = report_notify_channels(report)
        if "email" in channels:
            try:
                _deliver_email(report, filename, content, rows)
            except Exception as exc:  # noqa: BLE001 邮件失败不回滚产物
                errors.append(f"email: {exc}")
                logger.warning("scheduled report email failed: %s", report.pk, exc_info=True)
        errors.extend(_deliver_im(report, rows))
        bookkeep("SUCCESS" if not errors else "SUCCESS_WITH_DELIVERY_ERROR")
        record.error = "; ".join(errors)[:2000] if errors else None
        record.save(update_fields=["error", "updated_time"])
        logger.info("scheduled report done: %s rows=%s channels=%s", report.pk, rows, channels)
        return rows
    except TaskCancelled as exc:
        # 协作式取消（取消检查在 update_progress 里程碑内）：与异步导出同口径收敛为
        # REVOKED 终态——不是故障，不 re-raise（避免用户取消触发 task_failure 告警）
        record.status = ExportRecord.Status.REVOKED
        record.error = str(exc)[:2000]
        record.save(update_fields=["status", "error", "updated_time"])
        mark_execution_revoked(record.pk)
        logger.info("scheduled report cancelled by user: %s", record.pk)
        return 0
    except Exception as exc:
        record.status = ExportRecord.Status.FAILURE
        record.error = str(exc)[:2000]
        record.save(update_fields=["status", "error", "updated_time"])
        bookkeep("FAILURE")
        logger.warning("scheduled report failed: %s", report.pk, exc_info=True)
        raise


@shared_task
def schedule_report_run(report_id: str):
    """立即运行入口（管理页 run 动作）：预创建 ExportRecord 并按契约派发。

    手动运行属触发即执行，不推进调度簿记 last_run_at（到期判定不受影响，
    恰好落在到期点上的期次仍会被分发扫描投递）；last_status 照常写。
    """
    from dataset.models.dataset import Report

    report = Report.objects.filter(pk=report_id).first()
    if report is None:
        return ""
    task_id = _precreate_record(report)
    run_scheduled_report.apply_async(kwargs={"report_id": str(report.pk), "bookkeep_schedule": False}, task_id=task_id)
    return task_id


def _screen_has_viewers(layer, group) -> bool:
    """组内是否有在线展示连接（get_layers 为自定义 channel layer 扩展）。

    层未提供 get_layers（极简内存层）时保守视为在线：宁可多触发一次空推送，
    也不让观众在节流窗口内收不到数据。
    """
    getter = getattr(layer, "get_layers", None)
    if getter is None:
        return True
    return bool(async_to_sync(getter)(group))


@shared_task
@register_as_period_task(interval=15, description="大屏在线展示端周期数据推送", module="analysis")
def push_screen_data():
    """扫描大屏：向「有在线展示端且已到 refresh 周期」的屏投递数据触发事件。

    beat 只做「谁该刷」的节流判定（逐屏 last_push 缓存键 + cache.add 原子占位，
    见 ws_screen.screen_push_due / claim_screen_push），不做任何数据集查询；
    离屏（组内无连接）不触发也不占位，观众上线后能尽快收到首帧。屏数量小
    （模板级资源），MVP 全表扫描足够。
    """
    from channels.layers import get_channel_layer

    from dataset.models.dataset import Screen
    from dataset.ws_screen import (
        broadcast_screen_data_trigger,
        claim_screen_push,
        mark_screen_pushed,
        screen_group_name,
        screen_push_due,
    )

    layer = get_channel_layer()
    if layer is None:
        return 0
    pushed = 0
    for screen in Screen.objects.iterator():
        try:
            # 先判节流（一次 cache get）再查在线（一次 Redis 往返），离屏不占位不落节流键
            if not screen_push_due(screen):
                continue
            if not _screen_has_viewers(layer, screen_group_name(screen.pk)):
                continue
            # 原子占位（cache.add）：双 beat / 并发扫描只有一个赢者取得本轮广播权，
            # 落选者直接跳过——「查占用 + 占用」两步拆开会有双触发竞态窗口
            if not claim_screen_push(screen):
                continue
            broadcast_screen_data_trigger(screen.pk)
            mark_screen_pushed(screen.pk)
            pushed += 1
        except Exception:  # noqa: BLE001 单屏失败不中断扫描（与报表分发同口径）
            logger.warning("screen data push trigger failed: %s", screen.pk, exc_info=True)
    if pushed:
        logger.info("triggered screen data push for %s screen(s)", pushed)
    return pushed
