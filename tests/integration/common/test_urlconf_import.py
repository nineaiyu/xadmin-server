# -*- coding: utf-8 -*-
"""URLconf 首次加载不得触发数据字典读取（导入期实例化的序列化器）。

背景：ASGI 下首个请求在事件循环线程内加载 URLconf，app 导入链上的**导入期实例化**
（声明式嵌套序列化器、``@extend_schema`` 响应里的 schema 序列化器）若触发字典读取，
会被 Django 的 async_unsafe 拦截并降级为空——表现为每个进程启动后的首个请求刷出
若干条 ``data dict load failed`` 警告，且该次字典解析白白丢失。

本测试在子进程内复现该路径（asyncio 事件循环中加载 URLconf），断言期间字典解析
零调用：序列化器基类在事件循环线程内跳过字段收敛，字段绑定与字典解析留给请求线程
上按当次请求 deepcopy 出来的实例（见 packages/xadmin-common/common/core/serializers.py 的说明）。
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from django.conf import settings

REPO_ROOT = Path(__file__).resolve().parents[3]

PROBE = textwrap.dedent(
    """
    import asyncio
    import sys

    sys.path.insert(0, "__REPO_ROOT__")
    import django

    django.setup()

    # 计数而非只看日志：字典解析在事件循环线程内必然降级（拦截），同步线程内则可能成功
    from common.core import fields_dict

    calls = []
    resolver = fields_dict._dict_items_resolver

    def probe(code):
        calls.append(code)
        return resolver(code)

    fields_dict._dict_items_resolver = probe

    # 真实链路里中间件已在加载 URLconf 前绑定 current_request，序列化器基类据此
    # 判定"请求上下文存在"并继续字段收敛——探针必须复现该状态（否则空上下文会在
    # 更早的分支返回，测不出导入期读取）。
    class _DummyRequest:
        user = None
        fields = {}
        ignore_field_permission = False

    from common.local import set_current_request

    set_current_request(_DummyRequest())

    async def load_urlconf():
        from django.urls import get_resolver

        get_resolver().url_patterns

    asyncio.run(load_urlconf())
    print("DICT_CALLS=" + ",".join(calls))
    """
).replace("__REPO_ROOT__", str(REPO_ROOT))


def test_urlconf_import_in_event_loop_reads_no_dicts():
    # SETTINGS_MODULE 取三级兜底：显式环境变量 → 已配置 settings（override_settings 后可能
    # 是 UserSettingsHolder，取不到该属性）→ pytest.ini 缺省档
    settings_module = (
        os.environ.get("DJANGO_SETTINGS_MODULE") or getattr(settings, "SETTINGS_MODULE", None) or "tests.settings_real"
    )
    env = {key: value for key, value in os.environ.items() if value is not None}
    env["DJANGO_SETTINGS_MODULE"] = settings_module
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    marker = [line for line in result.stdout.splitlines() if line.startswith("DICT_CALLS=")]
    assert marker, result.stdout + result.stderr
    assert marker[0] == "DICT_CALLS=", f"URLconf 导入期触发了字典读取：{marker[0]}"
