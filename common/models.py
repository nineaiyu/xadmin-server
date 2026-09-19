#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : models
# author : ly_13
# date : 9/14/2024

from django.db import models
from django.utils.translation import gettext_lazy as _


class Monitor(models.Model):
    cpu_load = models.FloatField(verbose_name=_("CPU Load"), default=0)
    cpu_percent = models.FloatField(verbose_name=_("CPU Percent"), default=0)
    memory_used = models.FloatField(verbose_name=_("Memory Used"))
    disk_used = models.FloatField(verbose_name=_("Disk Used"), default=0)
    boot_time = models.FloatField(verbose_name=_("Boot Time"), default=0)
    # 网卡累计收发量（MB，psutil 计数器快照）：历史网络速率由相邻采样差分得出
    net_sent_mb = models.FloatField(verbose_name=_("Net sent (MB)"), default=0)
    net_recv_mb = models.FloatField(verbose_name=_("Net received (MB)"), default=0)
    created_time = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("Created time"))

    class Meta:
        verbose_name = _("Monitor")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.created_time}-{self.cpu_load}"


class MonitorAlert(models.Model):
    """资源告警记录（阈值检查的状态跃迁流水）。

    由 ServerPerformanceCheckUtil 每轮检查写入：指标越过阈值时新建/续写一条
    firing 记录（累计命中次数），指标回落时置为 resolved。同一指标同时只保留
    一条未恢复记录，避免 60s 检查周期刷出大量重复流水。
    """

    class Item(models.TextChoices):
        CPU_PERCENT = "cpu_percent", _("CPU percent")
        CPU_LOAD = "cpu_load", _("CPU load")
        MEMORY_USED = "memory_used", _("Memory used")
        DISK_USED = "disk_used", _("Disk used")

    class Status(models.TextChoices):
        FIRING = "firing", _("Firing")
        RESOLVED = "resolved", _("Resolved")

    item = models.CharField(max_length=32, choices=Item.choices, verbose_name=_("Alert item"))
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.FIRING, verbose_name=_("Status"))
    value = models.FloatField(verbose_name=_("Trigger value"))
    threshold = models.FloatField(verbose_name=_("Threshold"))
    message = models.TextField(verbose_name=_("Message"), blank=True, default="")
    count = models.PositiveIntegerField(default=1, verbose_name=_("Hit count"))
    first_time = models.DateTimeField(verbose_name=_("First time"))
    last_time = models.DateTimeField(verbose_name=_("Last time"))
    resolved_time = models.DateTimeField(null=True, blank=True, verbose_name=_("Resolved time"))
    created_time = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("Created time"))

    class Meta:
        verbose_name = _("Monitor alert")
        verbose_name_plural = verbose_name
        ordering = ("-last_time",)
        indexes = [
            models.Index(fields=["status", "last_time"], name="idx_monitoralert_status_time"),
            models.Index(fields=["item", "status"], name="idx_monitoralert_item_status"),
        ]

    def __str__(self):
        return f"{self.item}-{self.status}-{self.last_time}"
