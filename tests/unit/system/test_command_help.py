"""守护：所有本地自定义管理命令的 ``--help`` 必须能正常渲染。

背景：``argparse`` 的 ``help=`` 只接受 str。传 ``gettext_lazy`` 惰性对象时，
Python 3.12+ 的 argparse 在文本换行阶段会对代理对象做正则替换而抛
``TypeError: expected string or bytes-like object, got '__proxy__'``，
表现为 ``manage.py xxx --help`` 直接崩溃（命令本体却正常，容易被漏掉）。

本用例遍历仓库内自定义命令各渲染一次 help，避免同类缺陷回归：
新增命令后自动纳入覆盖，无需维护清单。
"""

import importlib
from pathlib import Path

import pytest
from django.core.management import load_command_class

# 仓库内的本地 app（第三方包不纳入：它们的 help 不归本项目负责）
LOCAL_APPS = ("system", "common", "captcha")


def _local_commands():
    commands = []
    for app in LOCAL_APPS:
        package = importlib.import_module(f"{app}.management.commands")
        for path in sorted(Path(package.__path__[0]).glob("*.py")):
            if not path.stem.startswith("_"):
                commands.append((app, path.stem))
    return commands


LOCAL_COMMANDS = _local_commands()
COMMAND_IDS = [f"{app}.{name}" for app, name in LOCAL_COMMANDS]


def test_local_commands_discovered():
    """命令发现机制本身要有产出（目录结构或包导入变化时能立刻暴露）。"""

    assert len(LOCAL_COMMANDS) > 10


@pytest.mark.parametrize(("app", "name"), LOCAL_COMMANDS, ids=COMMAND_IDS)
def test_command_help_renders(app, name):
    command = load_command_class(app, name)
    parser = command.create_parser("manage.py", name)

    text = parser.format_help()

    assert "usage:" in text
    # 命令自身的说明也要出现（覆盖 class 级 help 的惰性对象问题）。
    # argparse 会按终端宽度折行，因此按空白归一化后再比较
    assert " ".join(command.help.split()) in " ".join(text.split())
