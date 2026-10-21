#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据分析演示数据：批量创建 demo_ 前缀用户，撑起数据集/仪表盘/大屏的演示效果。

仅用于开发/演示环境——演示用户使用不可登录密码（unusable password），
不会进入正式初始化种子（load_init_json）。内置示例数据集「示例-全部用户」
的趋势卡按 date_joined 按月聚合，本命令把 date_joined 铺到近 12 个月，
性别/启用状态分布同步展开，图表即刻有形。

用法：

    python manage.py seed_demo_users             # 默认创建 128 个
    python manage.py seed_demo_users --count 300
    python manage.py seed_demo_users --reset     # 先删除 demo_ 前缀用户再创建
"""

import random
from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from identity.models import DeptInfo

DEMO_PREFIX = "demo_"


class Command(BaseCommand):
    help = "创建演示用户（demo_ 前缀，不可登录），供数据分析页面演示取数"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--count", type=int, default=128, help="创建数量（1-2000）")
        parser.add_argument("--reset", action="store_true", help="先删除已存在的 demo_ 前缀用户再创建")
        parser.add_argument("--clean-only", action="store_true", help="只删除批量演示用户（demo_数字 前缀），不创建")

    def _remove_batch_users(self) -> None:
        """仅清理批量演示用户（demo_ 后跟纯数字）：不触碰 demo_flow_* / demo_lead 等命令专用账号。"""
        user_model = get_user_model()
        queryset = user_model.all_objects.filter(username__regex=r"^demo_[0-9]+$")
        total = queryset.count()
        if not total:
            self.stdout.write("no demo batch users to remove")
            return
        deleted, _rows = queryset.delete()
        self.stdout.write(f"removed demo batch users: {deleted}")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["clean_only"]:
            self._remove_batch_users()
            return
        count = max(1, min(int(options["count"]), 2000))
        user_model = get_user_model()
        if options["reset"]:
            deleted, _ = user_model.objects.filter(username__startswith=DEMO_PREFIX).delete()
            self.stdout.write(f"removed existing demo users: {deleted}")

        demo_dept = DeptInfo.objects.filter(code="demo").first()
        depts = list(DeptInfo.objects.filter(code__in=("demo", "demo_rd", "demo_fin")).order_by("pk"))
        if not depts and demo_dept is not None:
            depts = [demo_dept]
        if not depts:
            self.stdout.write(self.style.WARNING("no demo dept found; demo users will have no dept"))
        elif demo_dept is not None:
            # 存量演示用户可能被旧种子轮询分配到真实部门：统一迁回演示子树，
            # 避免演示管理员的数据范围覆盖真实组织。
            subtree = set(DeptInfo.dept_tree_pks([demo_dept.pk])) | {dept.pk for dept in depts}
            stale = user_model.objects.filter(username__startswith=DEMO_PREFIX).exclude(dept_id__in=subtree)
            moved = 0
            for user in stale:
                user.dept = demo_dept
                user.save(update_fields=["dept", "updated_time"])
                moved += 1
            if moved:
                self.stdout.write(f"demo users moved into demo subtree: {moved}")

        # 固定随机种子：重复执行产出一致，便于文档/截图复现
        rng = random.Random(20260913)
        now = timezone.now()
        created = 0
        for i in range(1, count + 1):
            username = f"{DEMO_PREFIX}{i:04d}"
            if user_model.objects.filter(username=username).exists():
                continue
            # password=None → set_unusable_password，演示账号不可登录
            user = user_model.objects.create_user(username=username, password=None, nickname=f"演示用户{i:03d}")
            user.gender = rng.choice([1, 1, 1, 2, 2, 2, 0])
            user.is_active = rng.random() < 0.92
            user.phone = f"138{rng.randint(0, 99999999):08d}"
            if depts:
                user.dept = depts[i % len(depts)]
            # date_joined 铺满近 12 个月：趋势卡（按月聚合）即刻有 12 个桶
            user.date_joined = now - timedelta(days=rng.randint(0, 364), hours=rng.randint(0, 23))
            user.save()
            created += 1
        self.stdout.write(self.style.SUCCESS(f"demo users created: {created}"))
