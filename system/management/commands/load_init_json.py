#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : load_init_json
# author : ly_13
# date : 12/25/2023
import os.path

from django.conf import settings
from django.core.management.commands.loaddata import Command as LoadCommand
from django.db import DEFAULT_DB_ALIAS

from system.models import *


class Command(LoadCommand):
    help = 'load init json data'
    model_names = [UserRole, DeptInfo, Menu, MenuMeta, SystemConfig, DataPermission, FieldPermission, ModelLabelField]
    missing_args_message = None

    def add_arguments(self, parser):
        pass

    def handle(self, *args, **options):
        fixture_labels = []
        file_root = os.path.join(settings.BASE_DIR, "loadjson")
        for model in self.model_names:
            fixture_labels.append(os.path.join(file_root, f"{model._meta.model_name}.json"))
        options["ignore"] = ""
        options["database"] = DEFAULT_DB_ALIAS
        options["app_label"] = ""
        options["exclude"] = []
        options["format"] = "json"
        super(Command, self).handle(*fixture_labels, **options)
