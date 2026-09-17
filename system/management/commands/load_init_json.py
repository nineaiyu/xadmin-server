#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : load_init_json
# author : ly_13
# date : 12/25/2023
import os.path
import tempfile

from django.conf import settings
from django.core.management.commands.loaddata import Command as LoadCommand
from django.db import DEFAULT_DB_ALIAS
from django.db.models.signals import ModelSignal

from common.core.config import SysConfig
from common.core.modules import ModuleSeedFilter
from settings.models import Setting
from system.models import *
from system.utils.dict import invalid_dict_cache
from system.utils.seed import build_seed_fixtures


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
        # 数据分析内置示例：固定 pk 相互引用，必须按依赖顺序加载
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

        file_root = os.path.join(settings.PROJECT_DIR, "loadjson")
        options["ignore"] = ""
        options["database"] = DEFAULT_DB_ALIAS
        options["app_label"] = ""
        options["exclude"] = []
        options["format"] = "json"

        # 装配待导入的种子（system/utils/seed.py）：
        # 1. 功能模块裁剪：停用模块的菜单/权限点/字段权限绑定不入库（口径与运行期一致）；
        # 2. 冲突预检：自然键被库内数据占用时跳过该行（loaddata 是单事务，一行冲突会回滚全部）；
        # 未做任何裁剪且无冲突时直接用仓库里的原始种子文件
        with tempfile.TemporaryDirectory(prefix="xadmin_seed_") as target_dir:
            fixture_labels, notes, trimmed = build_seed_fixtures(
                self.model_names,
                file_root,
                target_dir,
                module_filter=ModuleSeedFilter.build(),
                using=DEFAULT_DB_ALIAS,
            )
            if notes:
                for note in notes:
                    self.stdout.write(self.style.WARNING(f"[种子冲突] {note}"))
                self.stdout.write(
                    self.style.WARNING(f"[种子冲突] 共跳过 {len(notes)} 处（库内数据优先，未改动库内对象）")
                )
            super().handle(*fixture_labels, **options)
        # 信号在导入期被整体屏蔽（含 DataDict post_save 失效钩子），而缓存后端
        # （Redis）跨进程存活：导入后主动全量失效，避免消费端拿到旧字典
        invalid_dict_cache()
        # 同理：loaddata 按 pk 覆盖 SystemConfig 字段且信号被屏蔽，种子更新后
        # 主动失效系统配置缓存，避免旧缓存压过新种子
        SysConfig.invalid_config_cache()
