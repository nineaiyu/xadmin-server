#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : tests
# author : ly_13
# date : 12/23/2023
import os
import secrets
import sys

ADMIN_PASSWORD_ENV = "XADMIN_ADMIN_PASSWORD"


def resolve_admin_password() -> str:
    """超管初始密码：环境变量 XADMIN_ADMIN_PASSWORD 优先，未设置时随机生成。

    随机密码仅在初始化输出中打印一次，首次登录后应立即修改；
    生产部署建议始终通过环境变量显式注入。
    """
    password = os.environ.get(ADMIN_PASSWORD_ENV, "").strip()
    if password:
        return password
    return secrets.token_urlsafe(16)


def main() -> None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")

    import django

    django.setup()

    from django.core import management

    from system.models import UserInfo

    # 如果有用户存在，则不支持初始化操作
    try:
        if UserInfo.objects.exists():
            print("User already exists")
            sys.exit(-1)
    except Exception as e:
        print(e)
        pass

    # 初始化操作
    try:
        management.call_command(
            "makemigrations",
        )
        management.call_command(
            "migrate",
        )
        # management.call_command('collectstatic', )
        management.call_command(
            "compilemessages",
        )
        management.call_command(
            "download_ip_db",
        )
    except Exception as e:
        print(f"Perform migrate failed, {e} exit")

    # 创建默认管理员用户：密码由 XADMIN_ADMIN_PASSWORD 注入，未设置则随机生成（仅打印一次）
    admin_password = resolve_admin_password()
    UserInfo.objects.create_superuser("xadmin", "xadmin@dvcloud.xin", admin_password)
    if os.environ.get(ADMIN_PASSWORD_ENV):
        print(f"Admin password loaded from {ADMIN_PASSWORD_ENV}")
    else:
        print(f"Admin password randomly generated (save it now, change after first login): {admin_password}")

    management.call_command(
        "load_init_json",
    )

    # 加载默认用户数据，一般部署新服的时候，如果有默认数据，则可以进行加载
    # management.call_command('loaddata', 'loadjson/userinfo.json')


if __name__ == "__main__":
    main()
