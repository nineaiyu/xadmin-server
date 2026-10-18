#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : load_init_json
# author : ly_13
# date : 12/25/2023
import tempfile
from typing import Any

from django.core.management.base import CommandError
from django.core.management.commands.loaddata import Command as LoadCommand
from django.db import DEFAULT_DB_ALIAS
from django.db.models.signals import ModelSignal

from common.core.modules import ModuleSeedFilter
from system.services.seeding import (
    SeedPlan,
    SeedPlanError,
    build_plan,
    discover,
    format_ownership,
    format_plan,
    format_problems,
    legacy_models,
    model_classes,
    prepare,
    validate_plan,
)
from system.services.seeding.rows import backfill_null_timestamps
from system.utils.platform.dict import invalid_dict_cache


class Command(LoadCommand):
    """装载内置种子（平台层 ``loadjson/`` + 各 app 的 ``seeds/``）。

    纪律（由守护测试锁定）：实例类数据（审批单 / 节点任务 / 审批请求）禁止入册——
    它们需要「申请人 ≠ 审批人」，种子造不出合规数据，由 ``seed_demo_flows`` 动态生成。
    """

    help = "load init json data"

    # 平台层历史装载顺序的唯一事实源在 system/services/seeding/legacy.py：
    # 字段树先于字段白名单、流程先于节点与版本、数据集先于看板与报表。
    model_names: list[Any] = list(legacy_models())

    missing_args_message = None

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--apps",
            default="",
            help="只装载指定应用（逗号分隔），自动补上 depends 依赖闭包",
        )
        parser.add_argument(
            "--with-sample",
            action="store_true",
            help="同时装载示例种子（seeds/sample/，默认不装）",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="打印装载地图（层 / 文件 / 模型 / 行数 / 顺序），不写库",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="只做结构与主键校验，不写库；有 error 时以非零码退出",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        want_list = bool(options.pop("list", False))
        want_check = bool(options.pop("check", False))
        with_sample = bool(options.pop("with_sample", False))
        apps = self._parse_apps(options.pop("apps", ""))

        # --list / --check 是只读诊断：始终展开示例层，否则 `--check` 会静默漏检
        # `<app>/seeds/sample/`（CI 门禁就失去意义）；真正装载仍按 --with-sample。
        inspect = want_list or want_check
        try:
            discovery = discover()
            plan = build_plan(discovery, apps=apps, include_sample=with_sample or inspect)
        except SeedPlanError as exc:
            raise CommandError(str(exc)) from exc

        if want_list:
            self.stdout.write(format_plan(plan, with_sample=with_sample or inspect))

        if want_list or want_check:
            result = validate_plan(plan, manifests=discovery.manifests)
            problems = [*discovery.problems, *result.problems]
            if want_check or result.has_error():
                self.stdout.write(format_problems(problems))
                self.stdout.write(format_ownership(result.ownership))
            if result.has_error():
                raise CommandError("种子校验未通过")
            return

        for problem in discovery.problems:
            self.stdout.write(self.style.WARNING(str(problem)))

        # 载入期屏蔽模型信号（loaddata 按 pk 覆盖不应触发失效/审计钩子）。屏蔽只限
        # 本次载入：收尾的内置角色补齐同步在信号恢复后执行（角色/菜单 m2m 变更的
        # 缓存失效钩子依赖它），否则进程内信号被永久替换（历史行为）
        ModelSignal.send = lambda *args, **kwargs: []  # 忽略任何信号
        try:
            self._load_seed(plan, *args, **options)
        finally:
            try:
                # send 定义在父类 Signal 上，删除子类遮蔽即恢复原方法
                del ModelSignal.send
            except AttributeError:
                pass
        self._sync_builtin_roles_after_seed()

    @staticmethod
    def _parse_apps(raw: Any) -> tuple[str, ...] | None:
        """``--apps a,b`` → ``("a", "b")``；未指定返回 ``None``（表示全部）。"""

        if not raw:
            return None
        apps = tuple(item.strip() for item in str(raw).split(",") if item.strip())
        return apps or None

    def _load_seed(self, plan: SeedPlan, *args: Any, **options: Any) -> None:
        options["ignore"] = ""
        options["database"] = DEFAULT_DB_ALIAS
        options["app_label"] = ""
        options["exclude"] = []
        options["format"] = "json"

        # 装配待导入的种子（system/services/seeding）：
        # 1. 功能模块裁剪：停用模块的菜单/权限点/字段权限绑定不入库（口径与运行期一致）；
        # 2. 冲突预检：自然键被库内数据占用时跳过该行（loaddata 是单事务，一行冲突会回滚全部）；
        # 未做任何裁剪且无冲突时直接用仓库里的原始种子文件
        with tempfile.TemporaryDirectory(prefix="xadmin_seed_") as target_dir:
            report = prepare(plan, target_dir, module_filter=ModuleSeedFilter.build(), using=DEFAULT_DB_ALIAS)
            self._report_notes(report, verbosity=int(options.get("verbosity", 1)))
            super().handle(*[str(label) for label in report.fixture_labels], **options)

        self._apply_grants(plan)

        # loaddata 以 raw 方式保存（跳过 pre_save），auto_now_add/auto_now 不生效：
        # 种子行缺失的 created_time/updated_time 落库为 NULL（列表页时间列空白），
        # 导入后统一回填（只补 NULL 行，不动已有时间）
        backfilled = backfill_null_timestamps(model_classes(plan))
        if backfilled:
            self.stdout.write(f"[种子时间] 回填 {backfilled} 处缺失的创建/更新时间")
        # 信号在导入期被整体屏蔽（含 DataDict post_save 失效钩子），而缓存后端
        # （Redis）跨进程存活：导入后主动全量失效，避免消费端拿到旧字典
        invalid_dict_cache()
        # 同理：loaddata 按 pk 覆盖 SystemConfig 字段且信号被屏蔽，
        # 种子更新后主动失效系统配置缓存，避免旧缓存压过新种子
        from common.core.config import SysConfig

        SysConfig.invalid_config_cache()
        # 菜单/角色授权在种子里也会被按 pk 覆盖（parent/path 等），而 Menu 的
        # post_save 失效钩子同样被屏蔽：不清路由与权限点缓存时，存量用户最长
        # 24 小时仍看到旧菜单树（改了种子却"没生效"的典型表现）
        self._invalidate_route_caches()

    def _report_notes(self, report: Any, *, verbosity: int = 1) -> None:
        """装配说明分流：真冲突保持 WARNING 并计数，策略性剥离只给普通提示。

        m2m 剥离是每次装载都会发生的预期行为（行照常导入），历史实现把它和唯一键
        冲突混在一起打印成「种子冲突 / 共跳过 N 处」，新装环境也会刷 4 条警告。
        这里按 ``SeedNote.kind`` 分开；未标类别的说明仍按冲突处理，避免把真问题
        降级成提示。
        """

        conflicts = report.conflict_notes
        prunes = report.prune_notes
        if conflicts or prunes:
            for note in [*conflicts, *prunes]:
                self.stdout.write(self.style.WARNING(f"[种子冲突] {note}"))
            summary = f"共跳过 {len(conflicts)} 行"
            if prunes:
                summary += f"、修剪 {len(prunes)} 处引用"
            self.stdout.write(self.style.WARNING(f"[种子冲突] {summary}（库内数据优先，未改动库内对象）"))

        strips = report.strip_notes
        if strips:
            self.stdout.write(
                f"[种子装配] 剥离 {len(strips)} 处角色菜单授权字段（行照常导入，授权由 grants.json 并集应用）"
            )
            if verbosity >= 2:
                for note in strips:
                    self.stdout.write(f"[种子装配] {note}")

    def _apply_grants(self, plan: SeedPlan) -> None:
        """装载后并集应用角色授权（``grants.json`` 声明）。

        不能随种子文件一起 loaddata：m2m 是覆盖式赋值，跨文件拆分会互相清零。
        缺失的菜单在模块裁剪场景下属预期（停用模块的菜单不入库），故只告警不中断。
        """
        from system.services.seeding.grants import apply_grants, load_grants

        declarations, problems = load_grants(plan.grants)
        for problem in problems:
            self.stdout.write(self.style.WARNING(str(problem)))
        report = apply_grants(declarations)
        self.stdout.write(f"[种子授权] {report.roles} 个角色 / {report.menus} 条菜单声明已并集应用（幂等，只增不删）")
        if report.missing_roles:
            self.stdout.write(self.style.WARNING(f"[种子授权] 角色不存在，跳过：{'、'.join(report.missing_roles)}"))
        if report.missing_menus:
            self.stdout.write(
                self.style.WARNING(f"[种子授权] {len(report.missing_menus)} 个菜单不在库中（停用模块或未装载），已跳过")
            )

    def _sync_builtin_roles_after_seed(self) -> None:
        """种子收尾补齐内置角色（幂等）。

        migrate 的 post_migrate 同步先于本命令执行：彼时 Menu 表与
        ModelLabelField 字段树均未种入，内置角色的菜单与字段白名单拿不到
        （SystemAdmin 全部活跃菜单、DeptManager 权限点清单 + userinfo/deptinfo
        字段白名单；字段权限 fail-closed，缺失 = 非超管接口输出空对象）。
        本命令恰好种入上述数据，收尾补跑一次同步使新装环境开箱可用。
        """
        from identity.builtin import sync_builtin_roles

        changed = sync_builtin_roles()
        self.stdout.write(f"[内置角色] 种子收尾同步完成（幂等，角色行变更 {changed}）")
        self._fill_field_permissions_after_seed()

    def _fill_field_permissions_after_seed(self) -> None:
        """种子收尾补齐非内置角色的字段白名单缺口（幂等）。

        字段权限为 fail-closed 的零字段口径：某 (角色, 权限点) 组合的白名单未覆盖该
        权限点绑定模型时，该角色在这些接口上输出空对象。内置角色由
        ``sync_builtin_roles`` 静态配置覆盖，而演示角色与二开新建角色**不在种子里**
        ——种子按 pk 重灌会覆盖运行期补齐的关系，缺口随之重现（升级后「演示账号审批
        列表空白」即此）。此处按审计结果补齐：只补无配置 / 与绑定模型脱节的组合，
        不改写管理员在角色页的人工裁剪。
        """
        from system.services.permission_sync import fill_field_permissions

        filled = fill_field_permissions()
        if filled:
            self.stdout.write(f"[字段权限] 种子收尾补齐 {len(filled)} 个（角色, 权限点）组合的全字段白名单（幂等）")

    @staticmethod
    def _invalidate_route_caches() -> None:
        from identity.models import UserInfo
        from identity.signal_handler import batch_invalid_cache

        pks = list(UserInfo.objects.values_list("pk", flat=True))
        if pks:
            batch_invalid_cache(pks)
