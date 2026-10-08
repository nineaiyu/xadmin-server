# -*- coding: utf-8 -*-
"""内核分发包（xadmin-common）发布口径守护。

守护面：

1. **版本单一事实源**：``common/__init__.py`` 的 ``__version__`` 是唯一写版本号的地方，
   成员 ``pyproject.toml`` 只能声明动态版本 + ``[tool.hatch.version]`` 指向它；
2. **四方一致**：源码 / changelog 首条 / 架构文档「当前」口径 / 发布脚本 ``check`` 全部对齐
   （changelog 条目按版本降序、日期 ISO、保留 ``Unreleased`` 段）；
3. **发布脚本契约**：``verify_wheel`` 的产物校验口径（缺文件 / 误带宿主内容即报）、
   ``publish`` 默认 dry-run（``--upload`` 才真上传）；
4. **CI 口径**：发布 workflow（``workflow_dispatch`` + secrets fail-fast）与 lint 的
   ``kernel-package`` job 都走同一脚本，避免文档 / CI / 脚本三处漂移。
"""

import importlib.util
import re
import sys
import tomllib
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBER_DIR = REPO_ROOT / "packages" / "xadmin-common"
INIT_FILE = MEMBER_DIR / "common" / "__init__.py"
CHANGELOG = MEMBER_DIR / "CHANGELOG.md"
PYPROJECT = MEMBER_DIR / "pyproject.toml"
KERNEL_DOC = REPO_ROOT / "docs" / "architecture" / "kernel-package.md"
RELEASE_DOC = REPO_ROOT / "docs" / "ops" / "kernel-release.md"
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "release_kernel.py"
LINT_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "lint.yml"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-kernel.yml"

_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)
_RELEASE_RE = re.compile(r"^##\s+\[(\d+\.\d+\.\d+)\]\s+-\s+(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

#: 内核 wheel 必须包含的成员（包本体 + 数据文件）
FULL_MEMBERS = [
    "common/__init__.py",
    "common/apps.py",
    "common/settings_contract.py",
    "common/templates/notify/msg_task.html",
    "common/migrations/0001_initial.py",
]


def _load_release_script():
    spec = importlib.util.spec_from_file_location("release_kernel_probe", RELEASE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_kernel_probe"] = module
    spec.loader.exec_module(module)
    return module


def _source_version() -> str:
    matched = _VERSION_RE.search(INIT_FILE.read_text(encoding="utf-8"))
    assert matched, "common/__init__.py 缺少 __version__（版本单一事实源）"
    return matched.group(1)


def _fake_wheel(path: Path, version: str, names: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "")
        archive.writestr(
            f"xadmin_common-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.3\nName: xadmin-common\nVersion: {version}\n",
        )
    return path


class TestVersionSingleSource:
    def test_source_version_is_semver(self):
        assert _SEMVER_RE.fullmatch(_source_version()), f"__version__ 不是 x.y.z：{_source_version()}"

    def test_pyproject_uses_dynamic_version_from_init(self):
        data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        assert "version" not in data["project"], "成员 pyproject 不得重复写版本号（事实源是 common/__init__.py）"
        assert "version" in data["project"].get("dynamic", []), '成员 pyproject 缺少 dynamic = ["version"]'
        hatch = data["tool"]["hatch"]["version"]
        assert hatch["path"] == "common/__init__.py", f"hatch 版本来源应指向 common/__init__.py：{hatch}"

    def test_changelog_released_entries_are_ordered_and_dated(self):
        text = CHANGELOG.read_text(encoding="utf-8")
        assert "## [Unreleased]" in text, "changelog 缺少 Unreleased 段（未发布改动归此段）"
        entries = _RELEASE_RE.findall(text)
        assert entries, "changelog 缺少形如 `## [0.1.0] - YYYY-MM-DD` 的发布条目"
        versions = [entry[0] for entry in entries]
        assert len(set(versions)) == len(versions), f"changelog 版本条目重复：{versions}"
        numeric = [tuple(int(part) for part in version.split(".")) for version in versions]
        assert numeric == sorted(numeric, reverse=True), f"changelog 版本条目未按降序排列：{versions}"
        assert entries[0][0] == _source_version(), (
            f"changelog 首条 {entries[0][0]} 与源码版本 {_source_version()} 不一致（发版先登记本文件）"
        )

    def test_kernel_doc_states_current_version(self):
        matched = re.search(r"当前\s+(\d+\.\d+\.\d+)", KERNEL_DOC.read_text(encoding="utf-8"))
        assert matched, "架构文档缺少「当前 x.y.z」版本口径"
        assert matched.group(1) == _source_version(), f"架构文档版本 {matched.group(1)} 与源码不一致"

    def test_release_script_check_passes(self):
        module = _load_release_script()
        assert module.read_source_version() == _source_version()
        assert module.main(["check"]) == 0


class TestReleaseScript:
    def test_verify_wheel_accepts_kernel_only_wheel(self, tmp_path):
        module = _load_release_script()
        wheel = _fake_wheel(tmp_path / "xadmin_common-0.9.9-py3-none-any.whl", "0.9.9", FULL_MEMBERS)
        assert module.verify_wheel(wheel, "0.9.9") == []

    def test_verify_wheel_flags_missing_members_and_host_leak(self, tmp_path):
        module = _load_release_script()
        partial = _fake_wheel(
            tmp_path / "partial.whl",
            "0.9.9",
            ["common/__init__.py", "common/apps.py", "common/settings_contract.py"],
        )
        problems = module.verify_wheel(partial, "0.9.9")
        assert any("templates/" in item for item in problems), problems
        assert any("migrations/" in item for item in problems), problems

        leaked = _fake_wheel(tmp_path / "leaked.whl", "0.9.9", [*FULL_MEMBERS, "server/settings/base.py"])
        assert any("宿主工程" in item for item in module.verify_wheel(leaked, "0.9.9"))

    def test_publish_defaults_to_dry_run(self, tmp_path, monkeypatch):
        module = _load_release_script()
        version = module.read_source_version()
        (tmp_path / f"xadmin_common-{version}-py3-none-any.whl").write_bytes(b"")
        (tmp_path / f"xadmin_common-{version}.tar.gz").write_bytes(b"")
        commands: list[list[str]] = []
        monkeypatch.setattr(module, "_run", lambda cmd: commands.append(list(cmd)))

        assert module.publish(tmp_path, url="https://pypi.example.com/legacy/", index=None, upload=False) == 0
        assert "--dry-run" in commands[-1]
        assert module.publish(tmp_path, url="https://pypi.example.com/legacy/", index=None, upload=True) == 0
        assert "--dry-run" not in commands[-1]

    def test_publish_without_target_is_refused(self, tmp_path, monkeypatch, capsys):
        module = _load_release_script()
        version = module.read_source_version()
        (tmp_path / f"xadmin_common-{version}-py3-none-any.whl").write_bytes(b"")
        monkeypatch.setattr(module, "_run", lambda cmd: None)
        monkeypatch.delenv(module.ENV_PUBLISH_URL, raising=False)
        monkeypatch.delenv(module.ENV_PUBLISH_INDEX, raising=False)
        assert module.publish(tmp_path, url=None, index=None, upload=False) == 1
        assert "未提供发布地址" in capsys.readouterr().out

    def test_release_doc_documents_env_vars(self):
        module = _load_release_script()
        text = RELEASE_DOC.read_text(encoding="utf-8")
        for name in (module.ENV_PUBLISH_URL, module.ENV_PUBLISH_INDEX, "UV_PUBLISH_TOKEN"):
            assert name in text, f"发布文档未登记环境变量 {name}"


class TestCiWiring:
    def test_lint_workflow_runs_kernel_package_gate(self):
        text = LINT_WORKFLOW.read_text(encoding="utf-8")
        assert "kernel-package:" in text, "lint.yml 缺少 kernel-package job"
        assert "scripts/release_kernel.py check" in text, "lint.yml 未跑版本一致性校验"
        assert "scripts/release_kernel.py build" in text, "lint.yml 未跑产物内容校验"

    def test_publish_workflow_is_manual_with_fail_fast_secrets(self):
        text = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
        assert "workflow_dispatch" in text, "发布 workflow 必须手工触发（workflow_dispatch）"
        assert "scripts/release_kernel.py publish" in text, "发布 workflow 未走统一发布脚本"
        assert "inputs.dry_run" in text, "发布 workflow 缺少 dry-run 开关"
        assert "secrets.KERNEL_PUBLISH_URL" in text and "secrets.KERNEL_PUBLISH_TOKEN" in text, (
            "发布 workflow 未登记凭据 secret（缺失即 fail-fast）"
        )
        assert "缺少 KERNEL_PUBLISH_URL secret" in text, "发布 workflow 未对缺失凭据 fail-fast"
