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

from approval.models import ApprovalFlow, ApprovalFlowNode, ApprovalFlowVersion
from common.core.config import SysConfig
from common.core.modules import ModuleSeedFilter
from dataset.models import Dashboard, Dataset, DynamicForm, DynamicFormSubmission, Report, Screen
from settings.models import Setting
from system.models import *
from system.utils.platform.dict import invalid_dict_cache
from system.utils.platform.seed import backfill_null_timestamps, build_seed_fixtures


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
        # 登录访问策略内置默认项（非工作时间要求二次验证 + 管理员登录留痕）：
        # 新装即具备基础安全策略；priority 取 500+ 让管理员自建策略（默认 100）先命中，
        # require_mfa 在用户无可用 MFA 方式时降级放行，不会造成登录死锁
        LoginAccessPolicy,
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
        # 载入期屏蔽模型信号（loaddata 按 pk 覆盖不应触发失效/审计钩子）。屏蔽只限
        # 本次载入：收尾的内置角色补齐同步在信号恢复后执行（角色/菜单 m2m 变更的
        # 缓存失效钩子依赖它），否则进程内信号被永久替换（历史行为）
        ModelSignal.send = lambda *args, **kwargs: []  # 忽略任何信号
        try:
            self._load_seed(*args, **options)
        finally:
            try:
                # send 定义在父类 Signal 上，删除子类遮蔽即恢复原方法
                del ModelSignal.send
            except AttributeError:
                pass
        self._sync_builtin_roles_after_seed()

    def _load_seed(self, *args, **options):
        file_root = os.path.join(settings.PROJECT_DIR, "loadjson")
        options["ignore"] = ""
        options["database"] = DEFAULT_DB_ALIAS
        options["app_label"] = ""
        options["exclude"] = []
        options["format"] = "json"

        # 装配待导入的种子（system/utils/platform/seed.py）：
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
        # loaddata 以 raw 方式保存（跳过 pre_save），auto_now_add/auto_now 不生效：
        # 种子行缺失的 created_time/updated_time 落库为 NULL（列表页时间列空白），
        # 导入后统一回填（只补 NULL 行，不动已有时间）
        backfilled = backfill_null_timestamps(self.model_names)
        if backfilled:
            self.stdout.write(f"[种子时间] 回填 {backfilled} 处缺失的创建/更新时间")
        # 信号在导入期被整体屏蔽（含 DataDict post_save 失效钩子），而缓存后端
        # （Redis）跨进程存活：导入后主动全量失效，避免消费端拿到旧字典
        invalid_dict_cache()
        # 同理：loaddata 按 pk 覆盖 SystemConfig 字段且信号被屏蔽，
        # 种子更新后主动失效系统配置缓存，避免旧缓存压过新种子
        SysConfig.invalid_config_cache()
        # 菜单/角色授权在种子里也会被按 pk 覆盖（parent/path 等），而 Menu 的
        # post_save 失效钩子同样被屏蔽：不清路由与权限点缓存时，存量用户最长
        # 24 小时仍看到旧菜单树（改了种子却"没生效"的典型表现）
        self._invalidate_route_caches()

    def _sync_builtin_roles_after_seed(self):
        """种子收尾补齐内置角色（幂等）。

        migrate 的 post_migrate 同步先于本命令执行：彼时 Menu 表与
        ModelLabelField 字段树均未种入，内置角色的菜单与字段白名单拿不到
        （SystemAdmin 全部活跃菜单、DeptManager 权限点清单 + userinfo/deptinfo
        字段白名单；字段权限 fail-closed，缺失 = 非超管接口输出空对象）。
        本命令恰好种入上述数据，收尾补跑一次同步使新装环境开箱可用。
        """
        from system.builtin import sync_builtin_roles

        changed = sync_builtin_roles()
        self.stdout.write(f"[内置角色] 种子收尾同步完成（幂等，角色行变更 {changed}）")

    @staticmethod
    def _invalidate_route_caches():
        from system.signal_handler import batch_invalid_cache

        pks = list(UserInfo.objects.values_list("pk", flat=True))
        if pks:
            batch_invalid_cache(pks)
