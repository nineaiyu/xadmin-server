#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析域共享可见性枚举（数据集 / 大屏 / 模板共用，避免模型模块循环 import）。"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class Visibility(models.TextChoices):
    PERSONAL = "personal", _("Personal")
    SHARED = "shared", _("Shared")
