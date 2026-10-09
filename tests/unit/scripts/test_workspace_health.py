# -*- coding: utf-8 -*-
"""工作区级跨仓一致性脚本（scripts/workspace_health.py）的行为测试。

脚本是「工作区健康」的裁判：同源面解析漂移、缺仓降级语义、矩阵空格检测任一处
出错都会给出误导性的全绿/全红。故这些分支必须有守护用例。用例用 tmp_path 造
迷你仓树，只驱动纯解析/判定逻辑与不发起子进程的面，不依赖真实五仓与外部工具。
"""

import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import workspace_health  # noqa: E402
from workspace_checks import (  # noqa: E402
    discovery,
    gate_runner,
    parity_contract,
    parity_csp,
    parity_locale,
    parity_permissions,
    parity_versions,
    report,
    textutil,
)

CSP_DIRECTIVES = {
    "default-src": ("'self'",),
    "script-src": ("'self'",),
    "connect-src": ("'self'", "ws:", "wss:"),
}


@pytest.fixture
def clean_env(monkeypatch):
    """清除全部跨仓路径覆盖变量，避免宿主环境污染用例。"""
    for key in discovery.REPO_ENV.values():
        monkeypatch.delenv(key, raising=False)


def write(root: Path, rel: str, text: str) -> Path:
    target = Path(root) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def csp_source(directives=None) -> str:
    directives = CSP_DIRECTIVES if directives is None else directives
    body = ",\n".join(f'    "{key}": {tuple(values)!r}' for key, values in directives.items())
    return f"_CSP_DIRECTIVES = {{\n{body}\n}}\n"


def nginx_conf(policy: str) -> str:
    return f'server {{\n    add_header Content-Security-Policy "{policy}";\n}}\n'


def mjs_csp(items) -> str:
    joined = ", ".join(json.dumps(item) for item in items)
    return f"const CSP = [{joined}].join('; ');\n"


def seed_versions(root: Path, *, server="4.2.5", client="4.2.5", docs="v4.2.5", installer="dev") -> Path:
    write(root, "xadmin-server/server/const.py", f'VERSION = "{server}"\n')
    write(root, "xadmin-client/package.json", json.dumps({"name": "x", "version": client}) + "\n")
    write(root, "xadmin-docs/guide/demo.md", f"```\nVERSION={docs}\n```\n")
    write(root, "xadmin-installer/static.env", f"export VERSION={installer}\n")
    return root


# ---------------------------------------------------------------------------
# textutil：与各仓既有守护脚本同口径的纯解析
# ---------------------------------------------------------------------------


class TestTextUtil:
    def test_parse_csp_directives_literal(self):
        assert textutil.parse_csp_directives(csp_source()) == CSP_DIRECTIVES

    def test_parse_csp_directives_non_literal_raises(self):
        with pytest.raises(ValueError):
            textutil.parse_csp_directives("_CSP_DIRECTIVES = dict(default_src=1)\n")

    def test_parse_csp_directives_missing_raises(self):
        with pytest.raises(ValueError):
            textutil.parse_csp_directives("OTHER = {}\n")

    def test_csp_expectation_shape(self):
        assert textutil.csp_expectation({"img-src": ("'self'", "data:")}) == {"img-src 'self' data:"}

    def test_split_policy_skips_report_uri_and_blanks(self):
        policy = "default-src 'self'; report-uri /api/csp; ; script-src 'self'"
        assert textutil.split_policy(policy) == {"default-src 'self'", "script-src 'self'"}

    def test_parse_nginx_csp_with_line(self):
        text = "line1\n" + nginx_conf("default-src 'self'")
        parsed = textutil.parse_nginx_csp(text)
        assert parsed == ("default-src 'self'", 3)

    def test_parse_nginx_csp_absent(self):
        assert textutil.parse_nginx_csp("server { listen 80; }\n") is None

    def test_parse_mjs_csp_joins_items(self):
        parsed = textutil.parse_mjs_csp(mjs_csp(["default-src 'self'", "script-src 'self'"]))
        assert parsed is not None
        assert parsed[0] == "default-src 'self'; script-src 'self'"

    def test_parse_yaml_keys_nested_paths(self):
        keys = textutil.parse_yaml_keys("system:\n  title: 系统\n  user:\n    add: 新增\n")
        assert keys == {"system", "system.title", "system.user", "system.user.add"}

    def test_parse_python_version(self):
        assert textutil.parse_python_version('VERSION = "4.2.5"\n') == "4.2.5"
        assert textutil.parse_python_version("OTHER = 1\n") is None

    def test_parse_json_version(self):
        assert textutil.parse_json_version('{"version": "4.2.5"}') == "4.2.5"
        assert textutil.parse_json_version("{not json") is None

    def test_parse_shell_env_version(self):
        assert textutil.parse_shell_env_version("export VERSION=dev\n") == "dev"

    def test_normalize_version_strips_v_and_blank(self):
        assert textutil.normalize_version("v4.2.5") == "4.2.5"
        assert textutil.normalize_version("  ") is None
        assert textutil.normalize_version(None) is None


# ---------------------------------------------------------------------------
# discovery：缺仓语义与跨仓环境变量
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_all_present(self, clean_env, tmp_path):
        for repo in discovery.ALL_REPOS:
            write(tmp_path, f"{repo}/{discovery.REPO_MARKERS[repo]}", "x")
        found = discovery.discover(tmp_path)
        assert found.missing == []
        assert all(found.is_present(repo) for repo in discovery.ALL_REPOS)

    def test_marker_missing_marks_absent(self, clean_env, tmp_path):
        write(tmp_path, "xadmin-server/server/const.py", "VERSION = '1'\n")
        (tmp_path / "xadmin-client").mkdir()
        found = discovery.discover(tmp_path)
        assert not found.is_present("xadmin-client")
        assert "xadmin-client" in found.missing

    def test_missing_not_allowed_gate(self, clean_env, tmp_path):
        found = discovery.discover(tmp_path, allow_missing={"xadmin-client"})
        assert ("xadmin-client" in found.allowed_missing) is True
        assert "xadmin-client" not in discovery.missing_not_allowed(found)
        assert "xadmin-server" in discovery.missing_not_allowed(found)

    def test_env_override_resolves_root(self, clean_env, monkeypatch, tmp_path):
        custom = tmp_path / "elsewhere"
        write(custom, "server/const.py", "VERSION = '1'\n")
        monkeypatch.setenv("XADMIN_SERVER_DIR", str(custom))
        assert discovery.detect_repo(tmp_path, "xadmin-server") == custom

    def test_env_exports_only_present(self, clean_env, tmp_path):
        write(tmp_path, "xadmin-server/server/const.py", "VERSION = '1'\n")
        found = discovery.discover(tmp_path)
        env = found.env(base={})
        assert env["XADMIN_SERVER_DIR"] == str(tmp_path / "xadmin-server")
        assert "XADMIN_CLIENT_DIR" not in env


# ---------------------------------------------------------------------------
# report：状态序、矩阵空格、退出码
# ---------------------------------------------------------------------------


class TestReportStatus:
    def test_worst_orders_fail_first(self):
        assert report.worst([report.STATUS_PASS, report.STATUS_FAIL]) == report.STATUS_FAIL
        assert report.worst([report.STATUS_DEGRADED, report.STATUS_PASS]) == report.STATUS_DEGRADED
        assert report.worst([]) == report.STATUS_PASS

    def test_repo_gap_degrades_only_when_allowed(self):
        assert report.repo_gap("x", "y", allowed=False).status == report.STATUS_MISSING
        assert report.repo_gap("x", "y", allowed=True).status == report.STATUS_DEGRADED


class TestMatrix:
    def test_build_matrix_fills_na_and_worst(self):
        specs = [report.FaceSpec("csp", ("a", "b"))]
        results = [
            report.CheckResult("csp", "i1", report.STATUS_PASS, repo="a"),
            report.CheckResult("csp", "i2", report.STATUS_FAIL, repo="a"),
        ]
        matrix = report.build_matrix(specs, ["a", "b", "c"], results)
        assert matrix.get("csp", "a") == report.STATUS_FAIL
        assert matrix.get("csp", "c") == report.STATUS_NA
        assert matrix.blanks() == [("csp", "b")]

    def test_blank_cell_is_coverage_gap(self):
        specs = [report.FaceSpec("csp", ("a",))]
        matrix = report.build_matrix(specs, ["a"], [])
        assert matrix.blanks() == [("csp", "a")]


class TestExitCode:
    @staticmethod
    def _health(results, blocking=()):
        return report.HealthReport(
            workspace_root="/w",
            checked_out=[],
            missing=[],
            allowed_missing=[],
            results=results,
            matrix=report.Matrix(faces=[], repos=[]),
            blocking_missing=list(blocking),
        )

    def test_all_pass_is_zero(self):
        assert self._health([report.CheckResult("c", "i", report.STATUS_PASS)]).exit_code() == 0

    def test_fail_beats_missing(self):
        results = [
            report.CheckResult("c", "i", report.STATUS_MISSING),
            report.CheckResult("c", "j", report.STATUS_FAIL),
        ]
        assert self._health(results).exit_code() == 1

    def test_missing_is_two(self):
        assert self._health([report.CheckResult("c", "i", report.STATUS_MISSING)]).exit_code() == 2

    def test_blocking_missing_without_result_is_two(self):
        """未被任何面引用的缺仓同样使退出码为 2（不得因面未覆盖而假绿）。"""
        assert (
            self._health([report.CheckResult("c", "i", report.STATUS_PASS)], blocking=["xadmin-web"]).exit_code() == 2
        )

    def test_json_and_renderers_expose_exit_code(self):
        health = self._health([report.CheckResult("c", "i", report.STATUS_PASS, findings=["ok"])])
        payload = json.loads(health.to_json())
        assert payload["exit_code"] == 0
        assert payload["results"][0]["face"] == "c"
        assert "退出码：0" in report.render_text(health)
        assert "| 面 |" in report.render_md(health)


# ---------------------------------------------------------------------------
# parity_csp：三处同源
# ---------------------------------------------------------------------------


def _csp_workspace(root: Path, *, web=True, client=True, policy=None, mjs_items=None) -> Path:
    write(root, "xadmin-server/server/const.py", 'VERSION = "4.2.5"\n')
    write(root, "xadmin-server/server/settings/csp.py", csp_source())
    if client:
        write(root, "xadmin-client/package.json", json.dumps({"name": "x", "version": "4.2.5"}) + "\n")
    if web:
        default_policy = (
            policy
            if policy is not None
            else "default-src 'self'; script-src 'self'; connect-src 'self' ws: wss:; report-uri /api/csp"
        )
        write(root, "xadmin-web/default.conf", nginx_conf(default_policy))
    if client:
        items = mjs_items
        if items is None:
            items = ["default-src 'self'", "script-src 'self'", "connect-src 'self' ws: wss:", "report-uri /api/csp"]
        write(root, "xadmin-client/scripts/csp-page-server.mjs", mjs_csp(items))
    return root


class TestParityCsp:
    def test_consistent_three_places(self, clean_env, tmp_path):
        _csp_workspace(tmp_path)
        results = {r.id: r for r in parity_csp.run(discovery.discover(tmp_path))}
        assert results["server-definition"].status == report.STATUS_PASS
        assert results["web-layer"].status == report.STATUS_PASS
        assert results["client-harness"].status == report.STATUS_PASS

    def test_drift_reports_missing_directive(self, clean_env, tmp_path):
        _csp_workspace(tmp_path, policy="default-src 'self'; report-uri /api/csp")
        results = {r.id: r for r in parity_csp.run(discovery.discover(tmp_path))}
        assert results["web-layer"].status == report.STATUS_FAIL
        assert any("script-src 'self'" in finding for finding in results["web-layer"].findings)

    def test_missing_web_is_missing_when_not_allowed(self, clean_env, tmp_path):
        _csp_workspace(tmp_path, web=False)
        results = {r.id: r for r in parity_csp.run(discovery.discover(tmp_path))}
        assert results["repo:xadmin-web"].status == report.STATUS_MISSING

    def test_missing_web_degrades_when_allowed(self, clean_env, tmp_path):
        _csp_workspace(tmp_path, web=False)
        found = discovery.discover(tmp_path, allow_missing={"xadmin-web"})
        results = {r.id: r for r in parity_csp.run(found)}
        assert results["repo:xadmin-web"].status == report.STATUS_DEGRADED
        assert any("CSP 页面层" in finding for finding in results["repo:xadmin-web"].findings)

    def test_unparsable_server_source_fails(self, clean_env, tmp_path):
        _csp_workspace(tmp_path)
        write(tmp_path, "xadmin-server/server/settings/csp.py", "_CSP_DIRECTIVES = dict(a=1)\n")
        results = {r.id: r for r in parity_csp.run(discovery.discover(tmp_path))}
        assert results["server-definition"].status == report.STATUS_FAIL


# ---------------------------------------------------------------------------
# parity_versions：版本矩阵
# ---------------------------------------------------------------------------


class TestParityVersions:
    def test_consistent_matrix(self, clean_env, tmp_path):
        seed_versions(tmp_path)
        results = {r.id: r for r in parity_versions.run(discovery.discover(tmp_path))}
        assert results["version:xadmin-client"].status == report.STATUS_PASS
        assert results["version:xadmin-docs"].status == report.STATUS_PASS
        assert results["version:xadmin-installer"].status == report.STATUS_PASS
        assert "开发豁免" in results["version:xadmin-installer"].note

    def test_client_drift_fails(self, clean_env, tmp_path):
        seed_versions(tmp_path, client="4.2.4")
        results = {r.id: r for r in parity_versions.run(discovery.discover(tmp_path))}
        assert results["version:xadmin-client"].status == report.STATUS_FAIL

    def test_missing_repo_is_missing(self, clean_env, tmp_path):
        seed_versions(tmp_path)
        (tmp_path / "xadmin-docs" / "guide" / "demo.md").unlink()
        results = {r.id: r for r in parity_versions.run(discovery.discover(tmp_path))}
        assert results["repo:xadmin-docs"].status == report.STATUS_MISSING


# ---------------------------------------------------------------------------
# parity_contract：normalize 口径（仅缩进差异视为一致）
# ---------------------------------------------------------------------------


class TestParityContract:
    def test_indent_only_difference_is_equal(self, clean_env, tmp_path):
        payload = {"type": "object", "properties": {"a": {"type": "string"}}}
        write(tmp_path, "xadmin-server/docs/schema/api-response.schema.json", json.dumps(payload, indent=4) + "\n")
        write(
            tmp_path,
            "xadmin-client/contract/schema/api-response.schema.json",
            json.dumps(payload, separators=(",", ":")),
        )
        server = tmp_path / "xadmin-server"
        client = tmp_path / "xadmin-client"
        found = discovery.discover(tmp_path, allow_missing=("xadmin-web",))
        result = parity_contract._mirror_compare(found, server, client)
        # 其余四份镜像缺失 → 仍 fail，但本份不得被判漂移
        assert not any("api-response: 镜像与服务端语义不一致" in f for f in result.findings)

    def test_semantic_difference_is_drift(self, clean_env, tmp_path):
        write(tmp_path, "xadmin-server/docs/schema/api-response.schema.json", json.dumps({"properties": {"a": 1}}))
        write(tmp_path, "xadmin-client/contract/schema/api-response.schema.json", json.dumps({"properties": {"a": 2}}))
        found = discovery.discover(tmp_path, allow_missing=("xadmin-web",))
        result = parity_contract._mirror_compare(found, tmp_path / "xadmin-server", tmp_path / "xadmin-client")
        assert result.status == report.STATUS_FAIL
        assert any("api-response: 镜像与服务端语义不一致" in f for f in result.findings)

    def test_server_source_completeness(self, clean_env, tmp_path):
        for name in parity_contract.SCHEMA_NAMES:
            write(tmp_path, f"xadmin-server/docs/schema/{name}.schema.json", "{}")
        assert parity_contract._server_source(tmp_path / "xadmin-server").status == report.STATUS_PASS
        (tmp_path / "xadmin-server/docs/schema/ws-frame.schema.json").unlink()
        assert parity_contract._server_source(tmp_path / "xadmin-server").status == report.STATUS_FAIL


# ---------------------------------------------------------------------------
# parity_permissions / parity_locale：种子 ↔ 前端消费
# ---------------------------------------------------------------------------


def _seed_menus(root: Path, title: str = "system.title") -> None:
    write(
        root,
        "xadmin-server/loadjson/menu.json",
        json.dumps([{"pk": 1, "model": "system.menu", "fields": {"meta": 10, "name": "system"}}]),
    )
    write(
        root,
        "xadmin-server/loadjson/menumeta.json",
        json.dumps([{"pk": 10, "model": "system.menumeta", "fields": {"title": title}}]),
    )


class TestParityPermissions:
    def test_all_keys_present_in_locales(self, clean_env, tmp_path):
        _seed_menus(tmp_path)
        write(tmp_path, "xadmin-client/locales/zh-CN.yaml", "system:\n  title: 系统\n")
        write(tmp_path, "xadmin-client/locales/en.yaml", "system:\n  title: System\n")
        result = parity_permissions._menumeta_locales(
            discovery.discover(tmp_path),
            tmp_path / "xadmin-server",
            tmp_path / "xadmin-client",
        )
        assert result.status == report.STATUS_PASS

    def test_missing_key_fails(self, clean_env, tmp_path):
        _seed_menus(tmp_path, title="system.absent")
        write(tmp_path, "xadmin-client/locales/zh-CN.yaml", "system:\n  title: 系统\n")
        write(tmp_path, "xadmin-client/locales/en.yaml", "system:\n  title: System\n")
        result = parity_permissions._menumeta_locales(
            discovery.discover(tmp_path),
            tmp_path / "xadmin-server",
            tmp_path / "xadmin-client",
        )
        assert result.status == report.STATUS_FAIL
        assert any("system.absent" in finding for finding in result.findings)

    def test_no_extractable_key_fails(self, clean_env, tmp_path):
        _seed_menus(tmp_path, title="系统管理")
        write(tmp_path, "xadmin-client/locales/zh-CN.yaml", "a: 1\n")
        write(tmp_path, "xadmin-client/locales/en.yaml", "a: 1\n")
        result = parity_permissions._menumeta_locales(
            discovery.discover(tmp_path),
            tmp_path / "xadmin-server",
            tmp_path / "xadmin-client",
        )
        assert result.status == report.STATUS_FAIL


class TestParityLocale:
    def test_yaml_asymmetry_fails(self, clean_env, tmp_path):
        write(tmp_path, "xadmin-client/locales/zh-CN.yaml", "a: 1\nb: 2\n")
        write(tmp_path, "xadmin-client/locales/en.yaml", "a: 1\n")
        result = parity_locale._yaml_symmetry(discovery.discover(tmp_path), tmp_path / "xadmin-client")
        assert result.status == report.STATUS_FAIL
        assert any("仅 zh-CN.yaml 存在" in f for f in result.findings)

    def test_yaml_symmetric_passes(self, clean_env, tmp_path):
        write(tmp_path, "xadmin-client/locales/zh-CN.yaml", "a: 1\nb: 2\n")
        write(tmp_path, "xadmin-client/locales/en.yaml", "a: 1\nb: two\n")
        result = parity_locale._yaml_symmetry(discovery.discover(tmp_path), tmp_path / "xadmin-client")
        assert result.status == report.STATUS_PASS


# ---------------------------------------------------------------------------
# gate_runner：缺仓降级、档位、shell 语法
# ---------------------------------------------------------------------------


class TestGateRunner:
    def test_missing_gate_repos_are_missing_or_degraded(self, clean_env, tmp_path):
        found = discovery.discover(tmp_path)  # 全缺，且未放行
        results = gate_runner.run(found, tier="fast")
        statuses = {r.repo: r.status for r in results if r.id.startswith("repo:")}
        assert statuses == {
            "xadmin-server": report.STATUS_MISSING,
            "xadmin-client": report.STATUS_MISSING,
            "xadmin-installer": report.STATUS_MISSING,
        }

        allowed = discovery.discover(tmp_path, allow_missing=gate_runner.REPOS)
        degraded = {r.repo: r.status for r in gate_runner.run(allowed, tier="fast") if r.id.startswith("repo:")}
        assert set(degraded.values()) == {report.STATUS_DEGRADED}

    def test_fast_tier_marks_skipped_full_gates(self, clean_env, tmp_path):
        write(tmp_path, "xadmin-server/server/const.py", "VERSION = '1'\n")
        found = discovery.discover(tmp_path, allow_missing=("xadmin-client", "xadmin-installer"))
        results = gate_runner.run(found, tier="fast")
        tier_lite = [r for r in results if r.id == "tier-lite"]
        assert len(tier_lite) == 1
        assert tier_lite[0].status == report.STATUS_DEGRADED

    def test_bash_syntax_detects_bad_script(self, clean_env, tmp_path):
        write(tmp_path, "xadmin-installer/static.env", "export VERSION=dev\n")
        write(tmp_path, "xadmin-installer/a.sh", "echo ok\n")
        write(tmp_path, "xadmin-installer/b.sh", "if [ 1; then\n")
        found = discovery.discover(tmp_path, allow_missing=("xadmin-server", "xadmin-client"))
        bash_results = [r for r in gate_runner.run(found, tier="fast") if r.id == "bash-syntax"]
        assert bash_results and bash_results[0].status == report.STATUS_FAIL
        assert any("b.sh" in finding for finding in bash_results[0].findings)

    def test_bash_syntax_passes_on_clean_tree(self, clean_env, tmp_path):
        write(tmp_path, "xadmin-installer/static.env", "export VERSION=dev\n")
        write(tmp_path, "xadmin-installer/a.sh", "set -e\necho ok\n")
        found = discovery.discover(tmp_path, allow_missing=("xadmin-server", "xadmin-client"))
        bash_results = [r for r in gate_runner.run(found, tier="fast") if r.id == "bash-syntax"]
        assert bash_results and bash_results[0].status == report.STATUS_PASS


# ---------------------------------------------------------------------------
# 入口：选择面、空格回填、面级异常收敛、缺仓退出码
# ---------------------------------------------------------------------------


class TestEntrypoint:
    def test_select_faces_only_and_skip(self):
        assert workspace_health.select_faces(types.SimpleNamespace(only="csp", skip="")) == ["csp"]
        assert workspace_health.select_faces(types.SimpleNamespace(only="", skip="gates")) == [
            "csp",
            "permissions",
            "contract",
            "locale",
            "versions",
        ]

    def test_unknown_face_rejected(self):
        with pytest.raises(SystemExit):
            workspace_health.select_faces(types.SimpleNamespace(only="nope", skip=""))

    def test_versions_only_run_is_green(self, clean_env, tmp_path, capsys):
        seed_versions(tmp_path)
        code = workspace_health.main(
            ["--workspace-root", str(tmp_path), "--only", "versions", "--allow-missing", "xadmin-web"]
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "工作区健康报告" in out
        assert "退出码：0" in out

    def test_unallowed_missing_repo_yields_two(self, clean_env, tmp_path, capsys):
        # 显式清空放行名单：未检出的 xadmin-web 不在任何选中面内，仍须使退出码为 2
        seed_versions(tmp_path)
        code = workspace_health.main(["--workspace-root", str(tmp_path), "--only", "versions", "--allow-missing", ""])
        assert code == 2
        capsys.readouterr()

    def test_json_report_written(self, clean_env, tmp_path, capsys):
        seed_versions(tmp_path)
        target = tmp_path / "report.json"
        workspace_health.main(
            [
                "--workspace-root",
                str(tmp_path),
                "--only",
                "versions",
                "--allow-missing",
                "xadmin-web",
                "--json",
                str(target),
                "--format",
                "json",
            ]
        )
        assert json.loads(target.read_text(encoding="utf-8"))["exit_code"] == 0
        capsys.readouterr()

    def test_json_format_stdout_is_pure_json(self, clean_env, tmp_path, capsys):
        """stdout 必须是纯 JSON：运行前骨架与告警分流到 stderr，可直接管道解析。"""
        seed_versions(tmp_path)
        code = workspace_health.main(
            [
                "--workspace-root",
                str(tmp_path),
                "--only",
                "versions",
                "--allow-missing",
                "xadmin-web",
                "--format",
                "json",
            ]
        )
        captured = capsys.readouterr()
        assert code == 0
        assert json.loads(captured.out)["exit_code"] == 0
        assert "工作区健康报告（运行前骨架）" in captured.err

    def test_blank_matrix_cell_becomes_fail(self, clean_env, tmp_path, capsys, monkeypatch):
        stub = types.SimpleNamespace(REPOS=("xadmin-server",), run=lambda ws, tier="fast": [])
        monkeypatch.setitem(workspace_health.FACE_MODULES, "versions", stub)
        write(tmp_path, "xadmin-server/server/const.py", "VERSION = '1'\n")
        code = workspace_health.main(
            ["--workspace-root", str(tmp_path), "--only", "versions", "--allow-missing", "xadmin-web"]
        )
        assert code == 1
        assert "矩阵空格" in capsys.readouterr().out

    def test_face_exception_becomes_fail_not_skip(self, clean_env, tmp_path, monkeypatch):
        def _boom(ws, tier="fast"):
            raise RuntimeError("解析炸了")

        stub = types.SimpleNamespace(REPOS=("xadmin-server",), run=_boom)
        monkeypatch.setitem(workspace_health.FACE_MODULES, "versions", stub)
        write(tmp_path, "xadmin-server/server/const.py", "VERSION = '1'\n")
        found = discovery.discover(tmp_path, allow_missing=("xadmin-web",))
        results = workspace_health.run_faces(["versions"], found, "fast", False)
        assert results[0].status == report.STATUS_FAIL
        assert results[0].id == "face-error"
        assert "RuntimeError" in results[0].findings[0]
