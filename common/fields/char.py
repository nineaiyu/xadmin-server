#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : aes
# author : ly_13
# date : 1/17/2024

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.base.utils import AESCipher


class AESField(models.Field):

    def __init__(self, *args, **kwargs):
        if 'prefix' in kwargs:
            self.prefix = kwargs['prefix']
            del kwargs['prefix']
        else:
            self.prefix = "aes:::"
        self.cipher = AESCipher(settings.SECRET_KEY)
        super(AESField, self).__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super(AESField, self).deconstruct()
        if self.prefix != "aes:::":
            kwargs['prefix'] = self.prefix
        return name, path, args, kwargs

    def from_db_value(self, value, *args, **kwargs):
        if value is None:
            return value
        if value.startswith(self.prefix):
            value = value[len(self.prefix):]
            if isinstance(value, str):
                value = value.encode('utf-8')
            value = self.cipher.decrypt(value)
        return value

    def to_python(self, value):
        if value is None:
            return value
        elif value.startswith(self.prefix):
            value = value[len(self.prefix):]
            if isinstance(value, str):
                value = value.encode('utf-8')
            value = self.cipher.decrypt(value)
        return value

    def get_prep_value(self, value):
        if isinstance(value, str):
            value = value.encode('utf-8')
        if isinstance(value, bytes):
            value = self.cipher.encrypt(value)
            value = self.prefix + value.decode('utf-8')
        elif value is not None:
            raise TypeError(_("{} is not a valid value for AESCharField").format(value))
        return value


class AESCharField(AESField, models.CharField):
    pass


class AESTextField(AESField, models.TextField):
    pass
