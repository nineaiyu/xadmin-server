#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : expire_config_caches
# author : ly_13
# date : 12/25/2023
from django.core.management.base import BaseCommand

from system.utils.modelfield import sync_model_field


class Command(BaseCommand):
    help = 'Sync Model Field'

    def handle(self, *args, **options):
        sync_model_field()
