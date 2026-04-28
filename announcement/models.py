from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


class Announcement(DbAuditModel):
    class StatusChoices(models.IntegerChoices):
        DRAFT = 0, _("草稿")
        PUBLISHED = 1, _("已发布")

    title = models.CharField(max_length=200, verbose_name=_("标题"))
    content = models.TextField(verbose_name=_("内容"))
    is_top = models.BooleanField(default=False, verbose_name=_("是否置顶"))
    is_published = models.BooleanField(default=False, verbose_name=_("是否发布"))
    status = models.SmallIntegerField(choices=StatusChoices, default=StatusChoices.DRAFT, verbose_name=_("状态"))
    publish_time = models.DateTimeField(null=True, blank=True, verbose_name=_("发布时间"))

    class Meta:
        verbose_name = _("系统公告")
        verbose_name_plural = verbose_name
        ordering = ['-is_top', '-publish_time', '-created_time']

    def __str__(self):
        return self.title
