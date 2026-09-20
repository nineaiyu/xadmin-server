#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键加载全部演示数据：图书示例 / 组织 / 审批 / 请假 / 内容 / 演示用户。

等价于依次执行：

    seed_demo_book     图书上架审批示例（菜单/权限点/流程/删除二次确认）
    seed_demo_org      组织 + 预置角色（四层权限）+ 场景模板
    seed_demo_flows    审批实例 + 轻量审批单 + 表单提交
    seed_demo_leave    请假业务闭环（通过/驳回/待审/草稿）
    seed_demo_content  通知公告 / 聊天室 / 知识库 / 文件 / 委托 / Webhook / 应用
    seed_demo_users    批量演示用户（撑起数据集的趋势与分布）

全程幂等，可重复执行；``--reset`` 先调用 ``seed_demo_clean`` 彻底清理再加载。
``seed_demo_clean`` 是对称的卸载入口（清理全部并回滚对内置种子的改写）。

用法：

    python manage.py seed_demo_all                 # 幂等加载（可重复执行）
    python manage.py seed_demo_all --reset         # 先彻底清理再加载
    python manage.py seed_demo_all --skip-users    # 跳过批量演示用户
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "一键加载全部演示数据（组织/审批/请假/内容/演示用户）；seed_demo_clean 为对称卸载入口"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true", help="先彻底清理全部演示数据再加载（等价于先跑 seed_demo_clean）"
        )
        parser.add_argument("--skip-users", action="store_true", help="跳过批量演示用户创建（seed_demo_users）")
        parser.add_argument("--users-count", type=int, default=128, help="批量演示用户数量（1-2000，默认 128）")

    def handle(self, *args, **options):
        if options["reset"]:
            call_command("seed_demo_clean")

        steps = [
            ("图书上架审批示例（demo app：菜单/权限点/流程/二次确认）", lambda: call_command("seed_demo_book")),
            ("组织与四层权限", lambda: call_command("seed_demo_org")),
            ("审批实例与表单提交", lambda: call_command("seed_demo_flows")),
            ("请假业务闭环", lambda: call_command("seed_demo_leave")),
            ("内容数据（通知/聊天/知识库/文件等）", lambda: call_command("seed_demo_content")),
        ]
        if not options["skip_users"]:
            steps.append(
                ("批量演示用户", lambda: call_command("seed_demo_users", count=options["users_count"])),
            )

        for label, run in steps:
            self.stdout.write(self.style.MIGRATE_HEADING(f"== {label} =="))
            run()
        self.stdout.write(self.style.SUCCESS("seed_demo_all done"))
