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
            "task": "system.utils.ctasks.auto_clean_tmp_file",
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
    from notifications.models import MessageContent, MessageUserRead
    from system.models import Menu, UserInfo, UserRole

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


def seed_monitor_scene():
    """监控页演示数据：近 24 小时心跳（5 分钟粒度）+ 一条未恢复磁盘告警。

    心跳走 create + update 强制回填 created_time（auto_now_add 不接受传入值）；
    网络累计量随序号递增，使相邻差分速率可算（趋势图的网络速率线才有值）。
    DAPHNE 环境不跑 gunicorn 心跳线程，没有这段数据趋势图恒为空态。
    """
    import math

    from django.utils import timezone

    from common.models import Monitor, MonitorAlert

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


def seed_demo_book_scene():
    """「二开样板页」场景：把生成器产出的菜单/权限点种子入库（防样板腐烂）。

    - 种子由 ``generate_crud demo.Book`` **现场生成到临时目录**（不落仓库），
      再 ``loaddata`` 入库：菜单结构与二开者照抄的产物同源，生成器改了结构
      而前端样板页没跟上时，对应 E2E 用例会立刻失败；
    - demo 已在测试 settings 的 ``XADMIN_APPS`` 内，API 与路由真实可用；
    - 失败只打印跳过（种子脚本的健壮性优先，用例侧断言会暴露缺失）。
    """

    import io
    import tempfile
    from pathlib import Path

    from django.core.management import call_command

    try:
        tmp_root = Path(tempfile.mkdtemp(prefix="e2e_demo_seed_"))
        call_command("generate_crud", "demo.Book", "--skip-frontend", output=str(tmp_root), stdout=io.StringIO())
        seed = tmp_root / "loadjson" / "seed_demo_book.json"
        if not seed.exists():
            print("skip demo book scene: 生成器未产出菜单种子")
            return
        call_command("loaddata", str(seed))
        print("demo book scene seeded (generator-produced menu + permissions)")
    except Exception as exc:  # noqa: BLE001 场景种子失败不影响主流程
        print(f"skip demo book scene: {exc}")


def seed_directory_scene():
    """通讯录场景：岗位维度数据（岗位 + 成员）。

    通讯录按部门/按岗位两种视角浏览人员：部门树复用既有 E2E 部门（主管场景已在
    用户种子中建好），岗位清单来自 search/post——这里补两个岗位并分配成员，
    让岗位视角在 E2E 有真实数据（而不是只有空态）。
    """
    from system.models import Post, UserInfo

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


def disable_login_mfa_policy():
    """停用内置「非工作时间登录需二次验证」策略（22:00-06:00 全用户）。

    该策略命中时登录返回 mfa_required，自动化无法完成邮箱验证码（locmem 后端），
    夜间跑批时全量登录用例会被拦截——与 tests/settings_e2e.py 关闭登录验证码 /
    传输加密同口径：E2E 环境放开登录辅助安全项（策略行为由后端集成测试覆盖）。
    """
    from system.models import LoginAccessPolicy

    disabled = LoginAccessPolicy.objects.filter(action=LoginAccessPolicy.Action.REQUIRE_MFA, is_active=True).update(
        is_active=False
    )
    print(f"login require_mfa policies disabled: {disabled}")
