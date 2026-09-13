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
from django.db.models.signals import ModelSignal

from settings.models import Setting
from system.models import *
from system.utils.dict import invalid_dict_cache


class Command(LoadCommand):
    help = "load init json data"
    model_names = [
        MenuMeta,
        Menu,
        SystemConfig,
        DataDict,
        DataPermission,
        UserRole,
        FieldPermission,
        ModelLabelField,
        DeptInfo,
        Setting,
        # 数据分析内置示例（ADR-020/021）：固定 pk 相互引用，必须按依赖顺序加载
        # Dataset(数据集) → Dashboard(仪表盘卡片引用数据集) → Screen(大屏引用仪表盘) / Report(报表外键数据集)
        Dataset,
        Dashboard,
        Screen,
        Report,
        # 脱敏/审批/表单采集内置示例：同上按依赖顺序加载。纪律（守护测试
        # test_builtin_seed.py）：
        # 1) 全部 creator=1（init_data 新库首个超管，任何环境都存在），禁止引用
        #    admin(pk=2) 等特定环境用户——E2E/新库没有该用户，loaddata 会炸；
        # 2) 实例类数据（ApprovalInstance/ApprovalNodeTask/ApprovalRequest）需要
        #    「申请人 ≠ 审批人」，种子造不出合规数据，由 seed_demo_flows 命令用
        #    真实引擎动态生成，严禁加入本清单；
        # 3) 流程节点 assignee_value 用 "xadmin,isummer" 双环境占位（字符串不做
        #    FK 校验），seed_demo_flows 会改写为演示审批人并落新版本快照。
        DataMaskRule,
        ApprovalFlow,
        ApprovalFlowNode,
        ApprovalFlowVersion,
        DynamicForm,
        DynamicFormSubmission,
    ]
    missing_args_message = None

    def add_arguments(self, parser):
        pass

    def handle(self, *args, **options):
        ModelSignal.send = lambda *args, **kwargs: []  # 忽略任何信号

        fixture_labels = []
        file_root = os.path.join(settings.PROJECT_DIR, "loadjson")
        for model in self.model_names:
            fixture_labels.append(os.path.join(file_root, f"{model._meta.model_name}.json"))
        options["ignore"] = ""
        options["database"] = DEFAULT_DB_ALIAS
        options["app_label"] = ""
        options["exclude"] = []
        options["format"] = "json"
        super(Command, self).handle(*fixture_labels, **options)
        # 信号在导入期被整体屏蔽（含 DataDict post_save 失效钩子），而缓存后端
        # （Redis）跨进程存活：导入后主动全量失效，避免消费端拿到旧字典
        invalid_dict_cache()
