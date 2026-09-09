#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据字典：业务可枚举选项的统一管理（字典类型 → 字典项两级）。

与 SystemConfig（键值配置）职责分离：字典服务「可枚举、带排序/分组的下拉选项」，
供表单 choices / 前端下拉消费（system/utils/dict.py 的 get_dict_items 带缓存）。

parent=None 为字典类型（code 全局唯一，代码层校验），parent 非空为字典项。
is_locked 标记被代码引用的内置字典，禁止删除与改 code（见 serializer / viewset 校验）。
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel


class DataDict(DbAuditModel, DbUuidModel):
    """数据字典（两级：类型 / 字典项）"""

    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        related_name="children",
        null=True,
        blank=True,
        verbose_name=_("Parent dict"),
    )
    code = models.CharField(_("Dict code"), max_length=64, db_index=True)
    label = models.CharField(_("Dict label"), max_length=128)
    label_en = models.CharField(_("Dict label (EN)"), max_length=128, blank=True, default="")
    value = models.CharField(_("Dict value"), max_length=255, blank=True, null=True)
    sort = models.IntegerField(_("Sort"), default=0)
    color = models.CharField(_("Tag color"), max_length=32, blank=True, null=True)
    is_active = models.BooleanField(_("Is active"), default=True)
    # 被代码引用的内置字典（user_gender / login_type / export_status 等）：禁止删除
    # 与改 code，避免误操作让 DictChoiceField 取不到选项、业务字段写入报 invalid_choice
    is_locked = models.BooleanField(_("Is locked"), default=False)

    class Meta:
        ordering = ("sort", "created_time")
        verbose_name = _("Data dict")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["code", "is_active"], name="idx_datadict_code_active")]
        constraints = [
            # 同一父节点下 code 唯一（字典项不重码）。必须带 condition：无条件的
            # UniqueConstraint 会让 DRF 唯一性校验把 parent 当必填，类型层（parent
            # 为空）创建直接 400；带 condition 时 DRF 跳过该约束校验，由 validate() 负责
            models.UniqueConstraint(
                fields=["parent", "code"],
                condition=Q(parent__isnull=False),
                name="uniq_datadict_parent_code",
            ),
            # 类型层 code 全局唯一：PG 的 UNIQUE 不约束 parent IS NULL 的行，
            # 由该部分索引兜底并发创建（validate() 显式查重无法覆盖竞态）
            models.UniqueConstraint(
                fields=["code"],
                condition=Q(parent__isnull=True),
                name="uniq_datadict_type_code",
            ),
        ]

    def __str__(self):
        return f"{self.label}({self.code})"

    def clean(self):
        # PG 的 UNIQUE(parent, code) 不约束 parent IS NULL 的行，类型层 code 全局唯一在此兜底
        if self.parent_id:
            return
        exists = DataDict.objects.filter(parent=None, code=self.code).exclude(pk=self.pk).exists()
        if exists:
            raise ValidationError({"code": _("Dict code already exists")})
