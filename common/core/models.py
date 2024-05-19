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


class SoftDeleteQuerySet(models.QuerySet):
    pass


class SoftDeleteManager(models.Manager):
    """
    软删除, 可用于用户和部门软删除，主要是有些数据和用户部门绑定，如果删除可能会丢失数据
    """

    def __init__(self, *args, **kwargs):
        self.__del_filter = False
        super(SoftDeleteManager, self).__init__(*args, **kwargs)

    def filter(self, *args, **kwargs):
        if kwargs.get('is_deleted'):
            self.__del_filter = True
        return super(SoftDeleteManager, self).filter(*args, **kwargs)

    def get_queryset(self):
        if self.__del_filter:
            return SoftDeleteQuerySet(self.model).filter(is_deleted=True)
        return SoftDeleteQuerySet(self.model).exclude(is_deleted=True)


class SoftDeleteModel(models.Model):
    show_delete_fields = []
    show_delete_prefix = 'is_deleted-'

    is_deleted = models.BooleanField(verbose_name="是否删除", default=False, db_index=True)
    objects = SoftDeleteManager()

    class Meta:
        abstract = True
        verbose_name = '软删除模型'
        verbose_name_plural = verbose_name

    def __getattribute__(self, item):
        if item in ['show_delete_prefix', 'show_delete_fields', 'is_deleted']:
            return super().__getattribute__(item)

        if self.show_delete_fields and item in self.show_delete_fields and self.is_deleted:
            item_value = super().__getattribute__(item)
            if item_value.startswith(self.show_delete_prefix):
                return item_value
            return f"{self.show_delete_prefix}{item_value}"

        return super().__getattribute__(item)

    def delete(self, using=None, keep_parents=False, rename_fields=None, update_dict=None, *args, **kwargs):
        """
        重写删除方法,直接开启软删除, rename_fields 需要重命名的字段，一般是防止唯一字段
        """
        update_fields = ['is_deleted', 'updated_time']
        self.is_deleted = True

        if rename_fields is None:
            rename_fields = []
            # 自动获取char类型的unique字段名字
            for field in self._meta.fields:
                if isinstance(field, models.CharField) and field.unique:
                    rename_fields.append(field.name)

        if rename_fields and isinstance(rename_fields, list):
            for field in rename_fields:
                setattr(self, field, f"{uuid.uuid4()}_{getattr(self, field).lstrip(self.show_delete_prefix)}")
                update_fields.append(field)

        if update_dict and isinstance(update_dict, dict):
            for field, value in update_dict.items():
                setattr(self, field, value)
                update_fields.append(field)
        self.save(using=using, update_fields=set(update_fields))
