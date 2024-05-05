#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : models
# author : ly_13
# date : 12/20/2023
import os
import time
import uuid

from django.conf import settings
from django.db import models


class DbUuidModel(models.Model):
    id = models.UUIDField(default=uuid.uuid4, primary_key=True, verbose_name="主键ID")

    class Meta:
        abstract = True


class DbCharModel(models.Model):
    id = models.CharField(primary_key=True, max_length=128, verbose_name="主键ID")

    class Meta:
        abstract = True


class DbBaseModel(models.Model):
    created_time = models.DateTimeField(auto_now_add=True, verbose_name="添加时间")
    updated_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    description = models.CharField(max_length=256, verbose_name="描述信息", null=True, blank=True)

    class Meta:
        abstract = True


class DbAuditModel(DbBaseModel):
    creator = models.ForeignKey(to=settings.AUTH_USER_MODEL, related_query_name='creator_query', null=True, blank=True,
                                verbose_name='创建人', on_delete=models.SET_NULL, related_name='+')
    modifier = models.ForeignKey(to=settings.AUTH_USER_MODEL, related_query_name='modifier_query', null=True,
                                 blank=True, verbose_name='修改人', on_delete=models.SET_NULL, related_name='+')
    dept_belong = models.ForeignKey(to="system.DeptInfo", related_query_name='dept_belong_query', null=True, blank=True,
                                    verbose_name='数据归属部门', on_delete=models.SET_NULL, related_name='+')

    class Meta:
        abstract = True


def upload_directory_path(instance, filename):
    prefix = filename.split('.')[-1]
    tmp_name = f"{filename}_{time.time()}"
    new_filename = f"{uuid.uuid5(uuid.NAMESPACE_DNS, tmp_name).__str__().replace('-', '')}.{prefix}"
    labels = instance._meta.label_lower.split('.')
    return os.path.join(labels[0], labels[1], str(instance.pk), new_filename)
