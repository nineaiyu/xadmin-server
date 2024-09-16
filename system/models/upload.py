#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : upload
# author : ly_13
# date : 8/10/2024

import hashlib

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import upload_directory_path, DbAuditModel


class UploadFile(DbAuditModel):
    filepath = models.FileField(verbose_name=_("Filepath"), null=True, blank=True, upload_to=upload_directory_path)
    file_url = models.URLField(verbose_name=_("Internet URL"), max_length=255, blank=True, null=True,
                               help_text=_("Usually an address accessible to the outside Internet"))
    filename = models.CharField(verbose_name=_("Filename"), max_length=255)
    filesize = models.IntegerField(verbose_name=_("Filesize"))
    mime_type = models.CharField(max_length=255, verbose_name=_("Mime type"))
    md5sum = models.CharField(max_length=36, verbose_name=_("File md5sum"))
    is_tmp = models.BooleanField(verbose_name=_("Tmp file"), default=False,
                                 help_text=_("Temporary files are automatically cleared by scheduled tasks"))
    is_upload = models.BooleanField(verbose_name=_("Upload file"), default=False)

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

    def __str__(self):
        return f"{self.filename}"
