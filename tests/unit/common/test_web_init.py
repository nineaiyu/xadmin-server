# -*- coding: utf-8 -*-
"""web 镜像 init.sh 的 X-Forwarded-Host 形态守护。

历史缺陷：HTTPS_PORT 只在 config_https() 内默认化，晚于 config_nginx() 的
「非标准端口」判断——默认 HTTP :80 部署下 `[ "${HTTPS_PORT}" != "443" ]` 恒真，
对外头被改写为 `$host:$server_port`；后端 `USE_X_FORWARDED_HOST` 无条件信任该头，
邀请 / 密码重置 / OAuth 回调等绝对 URL 会携带 `:80`。修复为在 config_nginx() 内
提前默认化；本用例在宿主上真实执行该函数（sed 用跨 BSD/GNU 的垫片），固化行为。
单仓检出（无 xadmin-web 兄弟仓）时跳过。
"""

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from django.conf import settings as dj_settings

# 只实现 init.sh 实际用到的 `s@...@...@g` 替换形态（-i 与管道两种调用），
# 用逐字符状态机解析（pattern / 替换串里可以含 `;`），遇到新形态直接报错，
# 迫使本垫片随 init.sh 演进同步。
SED_SHIM = textwrap.dedent(
    """
    import re
    import sys

    args = sys.argv[1:]
    in_place = args and args[0] == "-i"
    if in_place:
        args = args[1:]
    script, files = args[0], args[1:]

    def parse(script):
        cmds = []
        i, n = 0, len(script)
        while i < n:
            if script[i].isspace() or script[i] == ";":
                i += 1
                continue
            if script[i] != "s":
                raise SystemExit("unsupported sed command: " + script[i : i + 20])
            delim = script[i + 1]
            parts, cur, j = [], [], i + 2
            while j < n:
                ch = script[j]
                if ch == "\\\\" and j + 1 < n:
                    cur.extend((ch, script[j + 1]))
                    j += 2
                    continue
                if ch == delim:
                    parts.append("".join(cur))
                    cur = []
                    j += 1
                    if len(parts) == 2:
                        break
                    continue
                cur.append(ch)
                j += 1
            k = j
            while k < n and script[k] != ";":
                k += 1
            flags = script[j:k]
            if flags.strip("gi"):
                raise SystemExit("unsupported sed flags: " + flags)
            parts.append("".join(cur))
            cmds.append((parts[0], parts[1], flags))
            i = k
        return cmds

    def apply(text):
        for pattern, repl, flags in parse(script):
            text = re.compile(pattern, re.I if "i" in flags else 0).sub(
                repl.replace("\\\\", "\\\\\\\\"), text, count=0 if "g" in flags else 1
            )
        return text

    if in_place:
        for name in files:
            with open(name, encoding="utf-8") as fh:
                body = fh.read()
            with open(name, "w", encoding="utf-8") as fh:
                fh.write(apply(body))
    else:
        sys.stdout.write(apply(sys.stdin.read()))
    """
)


def _web_file(relative: str) -> Path:
    path = Path(dj_settings.PROJECT_DIR).parent / "xadmin-web" / relative
    if not path.exists():
        pytest.skip(f"单仓检出（缺 xadmin-web/{relative}），跳过 init.sh 头形态守护")
    return path


def _extract_config_nginx(init_sh: str) -> str:
    match = re.search(r"^config_nginx\(\) \{\n(.*?)^\}", init_sh, re.S | re.M)
    assert match, "init.sh 未找到 config_nginx() 函数（结构变更请同步本守护）"
    return "config_nginx() {\n" + match.group(1) + "}"


def _run_config_nginx(tmp_path: Path, env: dict) -> str:
    """在临时目录真实执行 init.sh 的 config_nginx()，返回改写后的 conf 文本。"""
    init_sh = _web_file("init.sh").read_text(encoding="utf-8")
    conf = tmp_path / "http_server.conf"
    conf.write_text(_web_file("http_server.conf").read_text(encoding="utf-8"), encoding="utf-8")
    shim = tmp_path / "sed_shim.py"
    shim.write_text(SED_SHIM, encoding="utf-8")
    func = tmp_path / "func.sh"
    func.write_text(_extract_config_nginx(init_sh), encoding="utf-8")

    harness = f'sed() {{ "{sys.executable}" "{shim}" "$@"; }}\n. "{func}"\nconfig_nginx "{conf}"\n'
    run_env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("HTTP_PORT", "HTTPS_PORT", "USE_LB", "SERVER_NAME", "CLIENT_MAX_BODY_SIZE"))
    }
    run_env.update(env)
    subprocess.run(["sh", "-c", harness], check=True, env=run_env, cwd=tmp_path)
    return conf.read_text(encoding="utf-8")


class TestInitForwardedHost:
    def test_default_http80_keeps_bare_host(self, tmp_path):
        """默认 HTTP :80 部署：HTTPS_PORT 未注入时不得给 X-Forwarded-Host 附加 :80。"""
        result = _run_config_nginx(tmp_path, {})
        assert "proxy_set_header X-Forwarded-Host $host;" in result
        assert "X-Forwarded-Host $host:$server_port" not in result

    def test_nonstandard_http_port_appends_port(self, tmp_path):
        """非标准 HTTP 端口：头携带端口（后端才能构建出带端口的绝对 URL）。"""
        result = _run_config_nginx(tmp_path, {"HTTP_PORT": "8080"})
        assert "listen 8080;" in result
        assert "proxy_set_header X-Forwarded-Host $host:$server_port;" in result

    def test_nonstandard_https_port_appends_port(self, tmp_path):
        """HTTPS 非标准端口（lb 形态注入 HTTPS_PORT）：头同样携带端口。"""
        result = _run_config_nginx(tmp_path, {"HTTPS_PORT": "8443"})
        assert "proxy_set_header X-Forwarded-Host $host:$server_port;" in result
