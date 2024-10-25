#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : sync_model_field
# author : ly_13
# date : 10/25/2024
from django.core.management.base import BaseCommand

from system.utils.modelfield import sync_model_field


class Command(BaseCommand):
    help = 'Sync Model Field'

    def handle(self, *args, **options):
        sync_model_field()
