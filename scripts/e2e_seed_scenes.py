#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""E2E 场景种子：任务/IM/通知/监控/演示书籍/通讯录等场景数据（自 e2e_seed.py 拆分，行为不变）。"""


def seed_periodic_task():
    """定时任务管理页 E2E 用的演示周期任务（默认停用，启停循环后复原）。"""
    from django_celery_beat.models import CrontabSchedule, PeriodicTask

    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="3",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
        defaults={"timezone": "Asia/Shanghai"},
    )
    PeriodicTask.objects.get_or_create(
        name="E2E-演示清理任务",
        defaults={
            "task": "system.tasks.auto_clean_tmp_file_job",
            "crontab": schedule,
            "enabled": False,
        },
    )


def seed_oauth_im_provider():
    """登录页第三方入口 E2E 数据：启用态飞书 flavor provider。

    只验证「配置 → 登录页可见」链路；回调交互依赖真实 IdP，由后端集成测试
    stub HTTP 覆盖。名称带 E2E 前缀，避免与真实配置混淆。
    """
    from common.core.config import SysConfig

    SysConfig.set_value(
        "OAUTH_PROVIDERS",
        [
            {
                "key": "feishu",
                "name": "E2E飞书",
                "flavor": "feishu",
                "client_id": "cli_e2e",
                "client_secret": "sec_e2e",
                "enabled": True,
            }
        ],
    )
    print("oauth im provider seeded (feishu flavor)")


def seed_user_notice_scene():
    """「我的通知」E2E 场景：给 e2e_user 授权通知页 + 两条未读通知。

    - 菜单授权：e2e_user 默认无角色（无任何页面权限），单独建角色授予
      「我的通知」页面 + 其下权限点（含 list / batchRead / allRead）；
    - 通知数据：USER 类型（发给指定用户）两条，标题固定便于用例定位；
      直接建 MessageUserRead 未读行，不依赖发布信号的推送链路（链路另有单测覆盖）。
    """
    from identity.models import UserInfo, UserRole
    from notifications.models import MessageContent, MessageUserRead
    from system.models import Menu

    user = UserInfo.objects.filter(username="e2e_user").first()
    page = Menu.objects.filter(path="/user/notice/index", menu_type=Menu.MenuChoices.MENU).first()
    if user is None or page is None:
        print("skip user notice scene: e2e_user or /user/notice/index menu missing")
        return

    role, _ = UserRole.objects.get_or_create(code="e2e_notice", defaults={"name": "E2E-通知体验"})
    menus = [page]
    if page.parent_id:
        menus.append(page.parent)
    menus.extend(Menu.objects.filter(parent=page, menu_type=Menu.MenuChoices.PERMISSION))
    role.menu.set(menus)
    user.roles.add(role)

    admin = UserInfo.objects.filter(username="xadmin").first()
    for index, title in enumerate(("E2E通知：系统升级预告", "E2E通知：本周例会安排")):
        content, _created = MessageContent.objects.get_or_create(
            title=title,
            defaults={
                "notice_type": MessageContent.NoticeChoices.USER,
                "level": MessageContent.LevelChoices.PRIMARY if index == 0 else MessageContent.LevelChoices.DEFAULT,
                "message": f"<p>{title}——这是 E2E 用例使用的未读通知，可在「我的通知」中查看。</p>",
                "publish": True,
                "creator": admin,
            },
        )
        MessageUserRead.objects.get_or_create(owner=user, notice=content, defaults={"unread": True})
    print("user notice scene seeded for e2e_user")


def seed_file_center_scene():
    """文件中心 E2E 场景：给 e2e_user 授权文件页面与页内全部权限点。

    非超管视角页面加载即请求统计（stats 由 retrieve 权限点覆盖）等接口：任一接口
    未授权都会产生 403，用例以「零 403 + 配额卡片渲染」断言收口（超管用例看不到
    这类「页面有授权、接口权限点漏配」的缺口）。字段白名单一并补齐：否则列表列
    与行内容按零字段 fail-closed 输出为空。
    """
    from identity.models import UserInfo, UserRole
    from system.models import Menu
    from system.services.permission_sync import fill_field_permissions

    user = UserInfo.objects.filter(username="e2e_user").first()
    page = Menu.objects.filter(path="/system/file/index", menu_type=Menu.MenuChoices.MENU).first()
    if user is None or page is None:
        print("skip file center scene: e2e_user or /system/file/index menu missing")
        return

    role, _ = UserRole.objects.get_or_create(code="e2e_file", defaults={"name": "E2E-文件中心"})
    menus = [page]
    if page.parent_id:
        menus.append(page.parent)
    menus.extend(Menu.objects.filter(parent=page, menu_type=Menu.MenuChoices.PERMISSION))
    role.menu.set(menus)
    user.roles.add(role)
    filled = fill_field_permissions(roles=[role])
    print(f"file center scene seeded for e2e_user (field whitelist filled: {len(filled)})")


def seed_monitor_scene():
    """监控页演示数据：近 24 小时心跳（5 分钟粒度）+ 一条未恢复磁盘告警。

    心跳走 create + update 强制回填 created_time（auto_now_add 不接受传入值）；
    网络累计量随序号递增，使相邻差分速率可算（趋势图的网络速率线才有值）。
    DAPHNE 环境不跑 gunicorn 心跳线程，没有这段数据趋势图恒为空态。
    """
    import math

    from django.utils import timezone

    from system.models import Monitor, MonitorAlert

    now = timezone.now()
    boot_time = (now - timezone.timedelta(days=3)).timestamp()
    points = 288
    for index in range(points, 0, -1):
        row = Monitor.objects.create(
            cpu_percent=round(38 + 16 * math.sin(index / 9), 2),
            cpu_load=round(1.2 + 0.6 * math.cos(index / 11), 2),
            memory_used=round(52 + 8 * math.sin(index / 13), 2),
            disk_used=round(62 + 3 * math.sin(index / 21), 2),
            boot_time=boot_time,
            net_sent_mb=round(4096 + index * 3.5, 2),
            net_recv_mb=round(8192 + index * 5.2, 2),
        )
        Monitor.objects.filter(pk=row.pk).update(created_time=now - timezone.timedelta(minutes=5 * index))

    MonitorAlert.objects.create(
        item="disk_used",
        value=88.6,
        threshold=80,
        message="Disk used more than 80%: => 88.6",
        count=6,
        first_time=now - timezone.timedelta(hours=2),
        last_time=now - timezone.timedelta(minutes=5),
    )
    print(f"monitor scene seeded: {points} heartbeat points + 1 firing alert")


def _generator_seed_files(root):
    """从生成器产物里挑出菜单/菜单元数据种子文件（按清单顺序，meta 先于 menu）。

    清单（``<app>/seeds/manifest.json``）是权威：应用级种子分层后规范文件名为
    ``menumeta.json`` / ``menu.json``，早期生成器写过 ``menumeta_<model>.json`` /
    ``menu_<model>.json``——两种命名都要能发现，否则场景种子会静默不装载
    （表现为二开样板页整页空白）。
    """

    import json

    manifests = sorted(root.glob("*/seeds/manifest.json"))
    if manifests:
        manifest = manifests[0]
        data = json.loads(manifest.read_text(encoding="utf-8"))
        entries = [
            entry
            for entry in data.get("files", [])
            if isinstance(entry, dict) and entry.get("model") in ("system.menumeta", "system.menu")
        ]
        entries.sort(key=lambda entry: (entry.get("order", 900), entry.get("name", "")))
        found = [path for path in (manifest.parent / str(entry["name"]) for entry in entries) if path.is_file()]
        if found:
            return found

    # 兼容旧布局：按模型拆分的 menu_<model>.json / menumeta_<model>.json
    # （「menumeta」也以 menu 开头，必须显式分档排序，否则菜单先装会因 meta 外键失败）
    legacy = [path for path in root.glob("*/seeds/*.json") if path.name != "manifest.json"]
    return sorted(
        (path for path in legacy if path.name.startswith(("menumeta", "menu"))),
        key=lambda path: (0 if path.name.startswith("menumeta") else 1, path.name),
    )


def seed_demo_book_scene():
    """「二开样板页」场景：把生成器产出的菜单/权限点种子入库（防样板腐烂）。

    - 种子由 ``generate_crud demo.Book`` **现场生成到临时目录**（不落仓库），
      再 ``loaddata`` 入库：菜单结构与二开者照抄的产物同源，生成器改了结构
      而前端样板页没跟上时，对应 E2E 用例会立刻失败；
    - demo 已在测试 settings 的 ``XADMIN_APPS`` 内，API 与路由真实可用；
    - 失败只打印跳过（种子脚本的健壮性优先，用例侧断言会暴露缺失）。

    产物发现见 :func:`_generator_seed_files`（读清单，兼容旧命名）。
    """

    import io
    import tempfile
    from pathlib import Path

    from django.core.management import call_command

    try:
        tmp_root = Path(tempfile.mkdtemp(prefix="e2e_demo_seed_"))
        call_command("generate_crud", "demo.Book", "--skip-frontend", output=str(tmp_root), stdout=io.StringIO())
        seeds = _generator_seed_files(tmp_root)
        if not seeds:
            print("skip demo book scene: 生成器未产出菜单种子")
            return
        call_command("loaddata", *[str(path) for path in seeds])
        print(f"demo book scene seeded (generator-produced {len(seeds)} seed files)")
    except Exception as exc:  # noqa: BLE001 场景种子失败不影响主流程
        print(f"skip demo book scene: {exc}")


def seed_directory_scene():
    """通讯录场景：岗位维度数据（岗位 + 成员）。

    通讯录按部门/按岗位两种视角浏览人员：部门树复用既有 E2E 部门（主管场景已在
    用户种子中建好），岗位清单来自 search/post——这里补两个岗位并分配成员，
    让岗位视角在 E2E 有真实数据（而不是只有空态）。
    """
    from identity.models import Post, UserInfo

    dev, _ = Post.objects.get_or_create(name="E2E研发岗", defaults={"code": "e2e_dev", "rank": 10})
    # 安全岗刻意用长名称（与演示库「示例-安全员（全组织）」同形）：名录列表视图的
    # 岗位标签必须在单元格内截断，长名称是这组回归断言的素材
    safety, _ = Post.objects.get_or_create(name="E2E-安全员（全组织演示）", defaults={"code": "e2e_safety", "rank": 20})
    for username in ("e2e_user", "e2e_member"):
        user = UserInfo.objects.filter(username=username).first()
        if user:
            dev.users.add(user)
    scoped = UserInfo.objects.filter(username="e2e_scoped").first()
    if scoped:
        safety.users.add(scoped)
    print("directory scene seeded (2 posts + members)")


def _ensure_menu(
    admin,
    *,
    name,
    path,
    title,
    rank,
    parent=None,
    menu_type=None,
    component="",
    **meta_extra,
):
    """幂等建菜单：同名即复用；meta_extra 直落 MenuMeta（徽标 / 分组 / 查询参数等）。"""
    from system.models import Menu, MenuMeta

    existing = Menu.objects.filter(name=name).first()
    if existing:
        return existing
    meta = MenuMeta.objects.create(title=title, creator=admin, **meta_extra)
    return Menu.objects.create(
        name=name,
        path=path,
        component=component,
        menu_type=menu_type or Menu.MenuChoices.MENU,
        rank=rank,
        parent=parent,
        meta=meta,
        creator=admin,
    )


def seed_menu_decoration_scene():
    """侧栏菜单装饰场景：徽标 / 分组 / 查询参数 / 菜单外页面高亮。

    在既有真实页面上挂一组「E2E菜单演示」菜单（rank 靠后，位于侧栏底部，
    同时充当「激活项自动滚入可视区」的素材）：

    - 「基础功能」组：文本徽标与红点徽标各一项；
    - 「高级功能」组：带查询参数（点击后 URL 应携带该参数）；
    - 隐藏项「E2E详情页」（菜单不可见）：activePath 指向徽标菜单，
      供「菜单外页面高亮指定菜单」用例直接访问（生产收录示例见
      loadjson 中个人中心菜单的 active_path 配置）。
    """
    from identity.models import UserInfo
    from system.models import Menu

    admin = UserInfo.objects.filter(username="xadmin").first()

    root = _ensure_menu(
        admin,
        name="E2EMenuDemo",
        path="/e2e-menu-demo",
        title="E2E菜单演示",
        rank=999800,
        menu_type=Menu.MenuChoices.DIRECTORY,
        icon="ep:menu",
        # 目录自身的红点：折叠态下唯一可见的徽标形态（子项未展开不渲染）
        badge="1",
        badge_type="dot",
    )
    _ensure_menu(
        admin,
        name="E2EMenuBadge",
        path="/e2e-menu-demo/badge",
        component="system/notice/index",
        title="E2E徽标菜单",
        rank=999801,
        parent=root,
        icon="ep:notification",
        badge="12",
        group_name="基础功能",
        group_rank=1,
    )
    _ensure_menu(
        admin,
        name="E2EMenuDot",
        path="/e2e-menu-demo/dot",
        component="system/user/index",
        title="E2E红点菜单",
        rank=999802,
        parent=root,
        icon="ep:view",
        badge="3",
        badge_type="dot",
        group_name="基础功能",
        group_rank=1,
    )
    _ensure_menu(
        admin,
        name="E2EMenuQuery",
        path="/e2e-menu-demo/query",
        component="system/role/index",
        title="E2E查询菜单",
        rank=999803,
        parent=root,
        icon="ep:files",
        query="tab=e2e",
        group_name="高级功能",
        group_rank=2,
    )
    _ensure_menu(
        admin,
        name="E2EMenuDetail",
        path="/e2e-menu-demo/detail",
        component="system/dept/index",
        title="E2E详情页",
        rank=999804,
        parent=root,
        icon="ep:document",
        is_show_menu=False,
        active_path="/e2e-menu-demo/badge",
    )
    print("menu decoration scene seeded (badge/group/query/activePath)")


def disable_login_mfa_policy():
    """停用内置「非工作时间登录需二次验证」策略（22:00-06:00 全用户）。

    该策略命中时登录返回 mfa_required，自动化无法完成邮箱验证码（locmem 后端），
    夜间跑批时全量登录用例会被拦截——与 tests/settings_e2e.py 关闭登录验证码 /
    传输加密同口径：E2E 环境放开登录辅助安全项（策略行为由后端集成测试覆盖）。
    """
    from identity.models import LoginAccessPolicy

    disabled = LoginAccessPolicy.objects.filter(action=LoginAccessPolicy.Action.REQUIRE_MFA, is_active=True).update(
        is_active=False
    )
    print(f"login require_mfa policies disabled: {disabled}")
