#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""图书上架审批示例（demo app）：流程定义 + 菜单/权限点 + 删除二次确认开关。

演示环境里「demo app 开箱可用」的入口，与其余 seed_demo_* 的分工：

- **流程定义**（code=demo_book，「示例-书籍上架」）：本命令灌入（不走 loadjson，
  生产库不出现示例流程）；提交上架后终态由信号回写 Book.status；
- **菜单与权限点**（示例目录 → 图书管理 + 权限点清单）：本命令灌入——demo 路由
  按既定口径不参与 loadjson 权限点扫描，权限点清单由本命令维护
  （``BookViewSet`` 新增 action 时必须同步扩充 ``PERMISSION_PLAN``）；
- **删除二次确认**：把 demo 书籍的删除 / 批量删除路径写入 ``APPROVAL_REQUIRED_PATHS``
  （敏感操作审批清单，--clean-only 时移除）——删除前先走审批（所有用户一律拦截、
  申请人不能自审），批准后重发请求由前端自动携带一次性令牌；
- **回收站 / 变更历史**：Book 为软删除模型（删除进回收站、可恢复 / 物理清除），
  权限点覆盖回收站三件套与行级变更历史（查操作日志端点）；
- **示例书籍（开箱即演示）**：3 条固定名称的书籍（幂等）；--clean-only 一并移除，
  库里无可用用户时自动跳过（不阻断菜单 / 流程等其余种子）；
- **周期任务（默认停用）**：「示例-自动下架书籍」指向 ``demo.tasks.auto_off_shelf_books``
  （任务管理页可启停 / 立即运行），--clean-only 时移除。

幂等：固定 pk（6eed000c 菜单段 / 6eed000d 流程段）update_or_create，可重复执行。

用法：

    python manage.py seed_demo_book                # 幂等生成
    python manage.py seed_demo_book --reset        # 先清理再生成
    python manage.py seed_demo_book --clean-only   # 只清理（seed_demo_clean 编排调用）
"""

import json

from django.core.management.base import BaseCommand

from common.core.config import SysConfig
from system.models import Menu, MenuMeta

# 固定 pk 段（与其他演示数据的 6eed 段区分）
DIR_PK = "6eed000c-0000-4000-8000-000000000001"
MENU_PK = "6eed000c-0000-4000-8000-000000000002"
DIR_META_PK = "6eed000c-0000-4000-8000-000000000003"
MENU_META_PK = "6eed000c-0000-4000-8000-000000000004"
FLOW_PK = "6eed000d-0000-4000-8000-000000000001"
NODE_PK = "6eed000d-0000-4000-8000-000000000002"
PERMISSION_PK_PREFIX = "6eed000c-0000-4000-8000-0000000001"
PERMISSION_META_PK_PREFIX = "6eed000c-0000-4000-8000-0000000002"

DIR_NAME = "demo"
DIR_PATH = "/demo"
MENU_NAME = "DemoBook"
MENU_PATH = "/demo/book/index"
MENU_COMPONENT = "demo/book/index"

FLOW_CODE = "demo_book"
FLOW_NAME = "示例-书籍上架"
NODE_NAME = "图书管理员审批"
# 审批人（双环境占位：超管 xadmin 通常存在；正式使用请改为实际用户/角色）
NODE_ASSIGNEE = "xadmin,isummer"

# 删除二次确认：写入敏感操作审批拦截清单的 demo 路径
# （与 demo/views.py 挂 ApprovalRequired 的 action 对应）
APPROVAL_PATTERNS = (
    "api/demo/book/(?P<pk>[^/.]+)$",  # 单条删除（DELETE destroy）
    "api/demo/book/batch-destroy$",  # 批量删除（POST batch_destroy）
)

# 示例书籍：固定名称清单（幂等识别 + --clean-only 精准移除）；开箱即可演示
# 「提交上架 / 删除审批 / 回收站 / 变更历史」，无需先手工造数据
DEMO_BOOKS = (
    ("《示例·快速开始》", "978-7-111-00001-1", "xadmin", 59.90, 0),
    ("《示例·权限与审批》", "978-7-111-00002-8", "xadmin", 79.90, 1),
    ("《示例·回收站与变更历史》", "978-7-111-00003-5", "xadmin", 99.90, 2),
)

# 周期任务种子（默认停用）：任务管理页可启停 / 立即运行，见 demo/tasks.py
PERIODIC_TASK_NAME = "示例-自动下架书籍"
PERIODIC_TASK_PATH = "demo.tasks.auto_off_shelf_books"
PERIODIC_TASK_KWARGS = {"days": 30}

# 权限点：(动作, 方法, 路径正则, 标题)，与 BookViewSet 的 action 一一对应；
# 新增 action 时同步扩充本清单（demo 路由不参与 loadjson 权限点扫描）
PERMISSION_PLAN = [
    ("list", "GET", "api/demo/book$", "Demo-书籍列表"),
    ("create", "POST", "api/demo/book$", "Demo-创建书籍"),
    ("retrieve", "GET", "api/demo/book/(?P<pk>[^/.]+)$", "Demo-书籍详情"),
    ("update", "PUT", "api/demo/book/(?P<pk>[^/.]+)$", "Demo-更新书籍"),
    ("partialUpdate", "PATCH", "api/demo/book/(?P<pk>[^/.]+)$", "Demo-局部更新书籍"),
    ("destroy", "DELETE", "api/demo/book/(?P<pk>[^/.]+)$", "Demo-删除书籍（可纳入审批）"),
    ("batchDestroy", "POST", "api/demo/book/batch-destroy$", "Demo-批量删除书籍（可纳入审批）"),
    ("push", "POST", "api/demo/book/(?P<pk>[^/.]+)/push$", "Demo-推送书籍"),
    ("submit", "POST", "api/demo/book/(?P<pk>[^/.]+)/submit$", "Demo-提交上架审批"),
    ("importData", "POST", "api/demo/book/import-data$", "Demo-导入书籍"),
    ("importHeaders", "POST", "api/demo/book/import-headers$", "Demo-导入表头"),
    ("importValidate", "POST", "api/demo/book/import-validate$", "Demo-导入校验"),
    ("importAsync", "POST", "api/demo/book/import-async$", "Demo-异步导入"),
    ("exportData", "GET", "api/demo/book/export-data$", "Demo-导出书籍"),
    ("exportAsync", "POST", "api/demo/book/export-async$", "Demo-异步导出"),
    # 回收站（软删除模型专用；Book 继承 SoftDeleteModel + 混入 RecycleBinAction）
    # 动作名与框架回收站权限点保持一致（其余模型均为 recycleList / recycleRestore /
    # recyclePurge）：前端按 `recycleList:Book` 判定入口可见性，名字不一致会永不显示
    ("recycleList", "GET", "api/demo/book/recycle$", "Demo-回收站列表"),
    ("recycleRestore", "PATCH", "api/demo/book/recycle/restore$", "Demo-回收站恢复"),
    ("recyclePurge", "DELETE", "api/demo/book/recycle/purge$", "Demo-回收站物理清除"),
    # 页面级权限（非 ViewSet action）：行级「变更历史」查操作日志端点
    ("changeHistory", "GET", "api/system/logs/operation$", "Demo-变更历史"),
]


class Command(BaseCommand):
    help = "生成图书上架审批示例（流程定义 + 菜单/权限点 + 删除二次确认开关），幂等"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="先清理示例数据再生成")
        parser.add_argument("--clean-only", action="store_true", help="只清理，不生成（seed_demo_clean 编排调用）")

    # ---------------------------------------------------------------- 清理

    def _reset(self):
        from approval.models.approval import ApprovalFlow, ApprovalInstance

        # 先清流程实例（ApprovalInstance.flow 为 PROTECT），再清流程定义（节点 CASCADE）
        removed = ApprovalInstance.objects.filter(biz_type="demo_book").delete()[0]
        self.stdout.write(f"removed demo book approval instances: {removed}")
        removed = ApprovalFlow.objects.filter(pk=FLOW_PK).delete()[0]
        self.stdout.write(f"removed demo book flow: {removed}")

        # 权限点（parent 悬空无妨）→ 菜单/目录/权限点 meta（meta 级联删菜单行）
        perm_pks = [f"{PERMISSION_PK_PREFIX}{index:02d}" for index in range(1, len(PERMISSION_PLAN) + 1)]
        removed = Menu.all_objects.filter(pk__in=perm_pks).delete()[0]
        meta_pks = [f"{PERMISSION_META_PK_PREFIX}{index:02d}" for index in range(1, len(PERMISSION_PLAN) + 1)]
        removed_meta = MenuMeta.objects.filter(pk__in=[DIR_META_PK, MENU_META_PK, *meta_pks]).delete()[0]
        self.stdout.write(f"removed demo book menus/meta: {removed} / {removed_meta}")

        self._update_approval_gate(remove=True)
        self._reset_demo_data()

    # ---------------------------------------------------------------- 流程 / 菜单 / 权限点

    def _ensure_flow(self):
        from approval.models.approval import ApprovalFlow, ApprovalFlowNode

        flow, _created = ApprovalFlow.objects.update_or_create(
            pk=FLOW_PK,
            defaults={
                "name": FLOW_NAME,
                "code": FLOW_CODE,
                "description": "内置示例（demo app）：图书上架审批，单节点；审批通过后书籍置为「已上架」",
                "form_schema": [
                    {"key": "name", "label": "书籍名称", "type": "text", "required": True, "options": []},
                    {"key": "isbn", "label": "标准书号", "type": "text", "required": False, "options": []},
                    {"key": "author", "label": "书籍作者", "type": "text", "required": False, "options": []},
                    {"key": "price", "label": "书籍售价", "type": "number", "required": False, "options": []},
                ],
                "is_active": True,
                "version": 1,
            },
        )
        ApprovalFlowNode.objects.update_or_create(
            pk=NODE_PK,
            defaults={
                "flow": flow,
                "name": NODE_NAME,
                "order": 1,
                "approve_type": "OR",
                "assignee_type": "user",
                "assignee_value": NODE_ASSIGNEE,
                "condition": {},
                "routes": [],
                "layout": {"x": 260, "y": 160},
                "timeout_hours": 24,
                "description": "图书管理员审批（示例）：默认取 xadmin/isummer，可在流程设计器调整",
            },
        )
        self.stdout.write(f"demo book flow ready: {FLOW_NAME}({FLOW_CODE})")

    def _ensure_menu(self):
        dir_meta, _ = MenuMeta.objects.update_or_create(
            pk=DIR_META_PK,
            defaults={"title": "示例", "icon": "ep:reading", "is_show_menu": True},
        )
        menu_meta, _ = MenuMeta.objects.update_or_create(
            pk=MENU_META_PK,
            defaults={"title": "图书管理（示例）", "icon": "ep:reading", "is_show_menu": True},
        )
        # all_objects + deleted_at=None：历史清理若只软删过菜单，本命令自动复活复用
        Menu.all_objects.update_or_create(
            pk=DIR_PK,
            defaults={
                "parent": None,
                "menu_type": 0,
                "name": DIR_NAME,
                "rank": 9998,
                "path": DIR_PATH,
                "component": "",
                "is_active": True,
                "deleted_at": None,
                "meta": dir_meta,
            },
        )
        Menu.all_objects.update_or_create(
            pk=MENU_PK,
            defaults={
                "parent_id": DIR_PK,
                "menu_type": 1,
                "name": MENU_NAME,
                "rank": 1,
                "path": MENU_PATH,
                "component": MENU_COMPONENT,
                "is_active": True,
                "deleted_at": None,
                "meta": menu_meta,
            },
        )
        for index, (action, method, path, title) in enumerate(PERMISSION_PLAN, start=1):
            meta, _ = MenuMeta.objects.update_or_create(
                pk=f"{PERMISSION_META_PK_PREFIX}{index:02d}",
                defaults={"title": title, "icon": "", "is_show_menu": True},
            )
            Menu.all_objects.update_or_create(
                pk=f"{PERMISSION_PK_PREFIX}{index:02d}",
                defaults={
                    "parent_id": MENU_PK,
                    "menu_type": 2,
                    "name": f"{action}:{MENU_NAME}",
                    "rank": index,
                    "path": path,
                    "component": None,
                    "is_active": True,
                    "deleted_at": None,
                    "method": method,
                    "meta": meta,
                },
            )
        self.stdout.write(f"demo book menu ready: {MENU_PATH}（{len(PERMISSION_PLAN)} 个权限点）")

    # ---------------------------------------------------------------- 示例数据 / 周期任务

    def _reset_demo_data(self):
        """移除示例书籍（物理删除，演示数据无保留价值）与周期任务种子。"""
        from django.apps import apps

        if not apps.is_installed("demo"):  # 未启用 demo 时无可清理的数据行，仅清周期任务
            self._reset_periodic_task()
            return
        from demo.models import Book

        names = [item[0] for item in DEMO_BOOKS]
        # all_objects 返回标准 QuerySet：delete() 为物理删除，关闭演示不留回收站残留
        removed = Book.all_objects.filter(name__in=names).delete()[0]
        self.stdout.write(f"removed demo books: {removed}")
        self._reset_periodic_task()

    def _reset_periodic_task(self):
        try:
            from django_celery_beat.models import PeriodicTask
        except ImportError:  # pragma: no cover - beat 未安装
            return
        removed = PeriodicTask.objects.filter(name=PERIODIC_TASK_NAME).delete()[0]
        self.stdout.write(f"removed demo periodic task: {removed}")

    def _ensure_demo_data(self):
        """3 条示例书籍：开箱即可演示（提交上架 / 删除 / 回收站 / 变更历史）。"""
        from django.apps import apps

        if not apps.is_installed("demo"):
            self.stdout.write("skip demo books: demo app 未启用（XADMIN_APPS 不含 demo；先启用再灌数据）")
            return
        from demo.models import Book
        from system.models import UserInfo

        admin = (
            UserInfo.objects.filter(is_superuser=True).order_by("pk").first() or UserInfo.objects.order_by("pk").first()
        )
        if admin is None:
            self.stdout.write("skip demo books: 当前库无可用用户（先创建超级管理员）")
            return
        created = 0
        for name, isbn, author, price, category in DEMO_BOOKS:
            book = Book.all_objects.filter(name=name).first()
            if book is None:
                Book(
                    name=name,
                    isbn=isbn,
                    author=author,
                    price=price,
                    category=category,
                    admin=admin,
                    admin2=admin,
                    dept_belong=admin.dept,
                    creator=admin,
                    modifier=admin,
                ).save()
                created += 1
            elif book.deleted_at is not None:
                # 曾在回收站的示例数据自动复活（deleted_at 清空；遵守软删基类 save 语义）
                book.deleted_at = None
                book.save(update_fields=["deleted_at"])
        self.stdout.write(f"demo books ready: {len(DEMO_BOOKS)} 条（新建 {created}）")

    def _ensure_periodic_task(self):
        """周期任务种子（默认停用）：任务管理页可启停 / 立即运行（见 demo/tasks.py）。"""
        try:
            from django_celery_beat.models import CrontabSchedule, PeriodicTask
        except ImportError:  # pragma: no cover - beat 未安装
            self.stdout.write("skip demo periodic task: django_celery_beat 未安装")
            return
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="30",
            hour="2",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            defaults={"timezone": "Asia/Shanghai"},
        )
        PeriodicTask.objects.update_or_create(
            name=PERIODIC_TASK_NAME,
            defaults={
                "task": PERIODIC_TASK_PATH,
                "crontab": schedule,
                "enabled": False,
                "kwargs": json.dumps(PERIODIC_TASK_KWARGS),
                "description": "示例：把上架超过 30 天的书籍自动下架（任务管理页可立即运行）",
            },
        )
        self.stdout.write(f"demo periodic task ready: {PERIODIC_TASK_NAME}（默认停用，任务管理页可立即运行）")

    # ---------------------------------------------------------------- 二次确认开关

    def _update_approval_gate(self, remove: bool = False):
        """把 demo 删除路径写入 / 移出敏感操作审批拦截清单（APPROVAL_REQUIRED_PATHS）。"""
        paths = [str(item) for item in (SysConfig.APPROVAL_REQUIRED_PATHS or []) if item]
        changed = False
        for pattern in APPROVAL_PATTERNS:
            if remove:
                if pattern in paths:
                    paths.remove(pattern)
                    changed = True
            elif pattern not in paths:
                paths.append(pattern)
                changed = True
        if changed:
            SysConfig.set_value("APPROVAL_REQUIRED_PATHS", paths)
            action = "removed" if remove else "enabled"
            self.stdout.write(f"approval required paths {action}: {list(APPROVAL_PATTERNS)}")
        else:
            self.stdout.write("approval required paths unchanged")

    # ---------------------------------------------------------------- 入口

    def handle(self, *args, **options):
        if options["reset"] or options.get("clean_only"):
            self._reset()
        if options.get("clean_only"):
            self.stdout.write("seed_demo_book clean-only done")
            return
        self._ensure_flow()
        self._ensure_menu()
        self._update_approval_gate()
        self._ensure_demo_data()
        self._ensure_periodic_task()
        self.stdout.write(self.style.SUCCESS("seed_demo_book done"))
