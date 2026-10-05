#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : upload
# author : ly_13
# date : 8/10/2024

import hashlib

from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import AutoCleanFileMixin, DbAuditModel, SoftDeleteModel, upload_directory_path
from file.utils.preview import remove_preview_cache_by_pk

# 非业务引用关系（related_name 口径）：审计 / 日志类关联表，不参与「附件是否在用」判定
NON_BUSINESS_RELATIONS = {"access_logs"}


class UploadFile(SoftDeleteModel, AutoCleanFileMixin, DbAuditModel):
    # 通用标签（白名单对象）
    tagged_items = GenericRelation("system.TaggedItem")
    filepath = models.FileField(verbose_name=_("Filepath"), null=True, blank=True, upload_to=upload_directory_path)
    file_url = models.URLField(
        verbose_name=_("Internet URL"),
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Usually an address accessible to the outside Internet"),
    )
    filename = models.CharField(verbose_name=_("Filename"), max_length=255)
    filesize = models.IntegerField(verbose_name=_("Filesize"))
    mime_type = models.CharField(max_length=255, verbose_name=_("Mime type"))
    md5sum = models.CharField(max_length=36, verbose_name=_("File md5sum"))
    is_tmp = models.BooleanField(
        verbose_name=_("Tmp file"),
        default=False,
        help_text=_("Temporary files are automatically cleared by scheduled tasks"),
    )
    is_upload = models.BooleanField(verbose_name=_("Upload file"), default=False)
    # 分类选项由数据字典 upload_category 提供（DictChoiceField 约束写入路径）
    category = models.CharField(
        verbose_name=_("Category"),
        max_length=32,
        blank=True,
        null=True,
        help_text=_("Category options come from the data dictionary upload_category"),
    )

    def file_still_referenced(self, file_field_name="filepath", file_name=None) -> bool:
        """磁盘删除守护：文件被别处引用时只删记录、保留磁盘文件（三期销项）。

        两类引用：
        1. 物理文件被其他活动记录共用（去重复用同一 filepath，或同 md5 的活动上传记录）——
           删掉一份记录不能连带删掉另一份仍在用的磁盘文件；
        2. 业务反向外键仍指向本条记录（如导出产物、导入源文件）——记录删除会 SET_NULL/级联，
           磁盘文件却可能被其他记录以同一路径引用，保守保留。

        调用方 = ``AutoCleanFileMixin``（删除或替换文件字段时）。
        """
        name = file_name or getattr(self.filepath, "name", None)
        if name:
            siblings = UploadFile.objects.filter(filepath=name).exclude(pk=self.pk)
            if self.md5sum:
                siblings = siblings | UploadFile.objects.filter(md5sum=self.md5sum, is_upload=True).exclude(pk=self.pk)
            if siblings.exists():
                return True
        return self.has_business_reference()

    def has_business_reference(self) -> bool:
        """是否存在业务模型（含软删除记录）指向本附件：存在即视为在用，保守保留磁盘文件。

        ``access_logs``（文件访问审计）不算业务引用：审计记录引用附件不代表
        附件仍在业务上使用，否则文件一旦被下载过就永远无法清理。
        """
        for relation in self._meta.related_objects:
            field_name = getattr(getattr(relation, "field", None), "name", None)
            if not field_name:
                continue
            if relation.get_accessor_name() in NON_BUSINESS_RELATIONS:
                continue
            related_model = relation.related_model
            manager = getattr(related_model, "all_objects", None) or related_model._default_manager
            try:
                if manager.filter(**{field_name: self.pk}).exists():
                    return True
            except Exception:  # noqa: BLE001 关系形态不适配（如自动生成的中间表）时跳过
                continue
        return False

    def hard_delete(self, *args, **kwargs):
        """物理删除：连带清理预览缓存（派生产物，源文件没了缓存即成孤儿）。

        只挂在硬删除：软删除可恢复，恢复后缓存仍可直接命中。
        """
        # Django 删除后会把 pk 置 None，缓存目录按 pk 推导，必须提前取
        pk = self.pk
        result = super().hard_delete(*args, **kwargs)
        remove_preview_cache_by_pk(pk)
        return result

    def save(self, *args, **kwargs):
        self.filename = self.filename[:255]
        if not self.md5sum and not self.file_url:
            md5 = hashlib.md5()
            for chunk in self.filepath.chunks():
                md5.update(chunk)
            if not self.filesize:
                self.filesize = self.filepath.size
            self.md5sum = md5.hexdigest()
        return super().save(*args, **kwargs)

    class Meta:
        verbose_name = _("Upload file")
        verbose_name_plural = verbose_name
        indexes = [
            # 列表过滤（is_tmp）与每日清理（is_tmp + created_time）共用复合索引；
            # md5sum 用于精确匹配（秒传/去重）
            models.Index(fields=["is_tmp", "created_time"], name="idx_uploadfile_tmp_created"),
            models.Index(fields=["md5sum"], name="idx_uploadfile_md5sum"),
            # 个人配额聚合（creator 维度 Sum/Count）
            models.Index(fields=["creator", "created_time"], name="idx_uploadfile_creator_created"),
            # 全局搜索的 pg_trgm 索引（文件名 icontains；PostgreSQL 生效，见 system/search_indexes.py）
            GinIndex(fields=["filename"], name="idx_uploadfile_filename_trgm", opclasses=["gin_trgm_ops"]),
        ]

    def __str__(self):
        return f"{self.filename}"


class UploadSession(DbAuditModel):
    """分片上传会话：大文件分片/断点续传协议的会话侧记录（协议见 system/utils/file/upload_chunk.py）。

    会话只承载「传输中」状态，不承载文件本体：分片写入存储的
    ``upload_sessions/<pk>/part-<index>``，完成时合并并经既有上传内核
    （``store_upload_file``）落库为 :class:`UploadFile`——安全校验（扩展名 /
    大小 / 配额）、去重与分类全部复用同一管线，不出现第二套口径。

    断点续传 = 客户端对同名同大小文件重新 init 时命中未完成会话，按已收
    分片索引续传；过期未完成会话由 ``auto_clean_upload_sessions`` 定时清理。
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        COMPLETED = "completed", _("Completed")
        ABORTED = "aborted", _("Aborted")

    filename = models.CharField(verbose_name=_("Filename"), max_length=255)
    # 大文件口径：整型上限（int32 ≈ 2.1GB）不够，会话侧用 bigint；最终落库仍受
    # FILE_UPLOAD_SIZE 政策上限约束（init 期校验）
    filesize = models.BigIntegerField(verbose_name=_("Filesize"))
    total_chunks = models.IntegerField(verbose_name=_("Total chunks"))
    chunk_size = models.IntegerField(verbose_name=_("Chunk size"))
    # 客户端可选声明的内容指纹（整文件 md5）：complete 时一致性校验，为空跳过
    md5sum = models.CharField(max_length=36, verbose_name=_("File md5sum"), blank=True, default="")
    mime_type = models.CharField(max_length=255, verbose_name=_("Mime type"), blank=True, default="")
    status = models.CharField(verbose_name=_("Status"), max_length=16, choices=Status.choices, default=Status.PENDING)
    upload = models.ForeignKey(
        UploadFile,
        verbose_name=_("Upload file"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="upload_sessions",
    )

    class Meta:
        verbose_name = _("Upload session")
        verbose_name_plural = verbose_name
        indexes = [
            # 断点续传命中：同属主同名同大小的未完成会话
            models.Index(fields=["creator", "filename", "filesize", "status"], name="idx_upsess_resume"),
            # 过期清理（pending 会话 + 终态会话行）按时间扫描
            models.Index(fields=["status", "created_time"], name="idx_upsess_status_created"),
        ]

    def __str__(self):
        return f"{self.filename}({self.status})"


class UploadSessionPart(models.Model):
    """分片上传会话的单个已收分片（存在性 + 尺寸记录；分片本体在存储侧）。

    独立行而非会话行上的 JSON 列表：并发上传不同分片互不覆盖（唯一约束兜底
    重复传输），complete 校验「行数 = 总分片数且尺寸之和 = 文件大小」。
    """

    session = models.ForeignKey(
        UploadSession, verbose_name=_("Upload session"), on_delete=models.CASCADE, related_name="parts"
    )
    index = models.IntegerField(verbose_name=_("Chunk index"))
    size = models.IntegerField(verbose_name=_("Chunk size"))

    class Meta:
        verbose_name = _("Upload session part")
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=["session", "index"], name="uniq_uploadsession_part_index")]

    def __str__(self):
        return f"{self.session_id}#{self.index}"
