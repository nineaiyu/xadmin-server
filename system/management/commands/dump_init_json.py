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

from system.models import *


class Command(BaseCommand):
    help = 'dump init json data'
    model_names = [UserRole, DeptInfo, Menu, MenuMeta, SystemConfig, DataPermission, FieldPermission, ModelLabelField]

    def save_json(self, queryset, filename):
        stream = open(filename, 'w', encoding='utf8')
        try:
            serializers.serialize(
                'json',
                queryset,
                indent=4,
                stream=stream or self.stdout,
                object_count=queryset.count(),
            )
        except Exception as e:
            print(f"{queryset.model._meta.model_name} {filename} dump failed {e}")
        finally:
            if stream:
                stream.close()

    def handle(self, *args, **options):
        file_root = os.path.join(settings.BASE_DIR, "loadjson")
        for model in self.model_names:
            self.save_json(model.objects.all(), os.path.join(file_root, f"{model._meta.model_name}.json"))
