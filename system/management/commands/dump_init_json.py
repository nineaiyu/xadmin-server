#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : dump_init_json
# author : ly_13
# date : 12/25/2023
import os.path

from django.conf import settings
from django.core import serializers
from django.core.management.base import BaseCommand

from settings.models import Setting
from system.models import *


def get_fields(model):
    if issubclass(model, FieldPermission):
        exclude_fields = ['updated_time', 'created_time']
    elif issubclass(model, ModelLabelField):
        exclude_fields = ['updated_time']
    else:
        exclude_fields = []

    return [x.name for x in model._meta.get_fields() if x.name not in exclude_fields]


class Command(BaseCommand):
    help = 'dump init json data'
    model_names = [UserRole, DeptInfo, Menu, MenuMeta, SystemConfig, DataPermission, FieldPermission, ModelLabelField,
                   Setting]

    def save_json(self, queryset, filename):
        stream = open(filename, 'w', encoding='utf8')
        try:
            serializers.serialize(
                'json',
                queryset,
                indent=2,
                stream=stream or self.stdout,
                object_count=queryset.count(),
                fields=get_fields(queryset.model)
            )
        except Exception as e:
            print(f"{queryset.model._meta.model_name} {filename} dump failed {e}")
        finally:
            if stream:
                stream.close()

    def handle(self, *args, **options):
        file_root = os.path.join(settings.PROJECT_DIR, "loadjson")
        for model in self.model_names:
            self.save_json(model.objects.all().order_by('pk'),
                           os.path.join(file_root, f"{model._meta.model_name}.json"))
