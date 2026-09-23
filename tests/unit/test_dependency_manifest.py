#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""依赖清单三方一致性守护（pyproject + uv.lock + requirements 产物）。

事实源：``pyproject.toml``（运行依赖 [project].dependencies / 开发依赖 [dependency-groups].dev）
产物：``requirements.txt`` / ``requirements-dev.txt``（``uv export`` 输出，**勿手工编辑**）
锁：``uv.lock``

守护面（纯解析，不依赖 uv 二进制与网络）：
1. 产物每行必须是 ``name[extras]==version[ ; marker]`` 形态（防手写非法行）；
2. 产物包集合与版本必须与 uv.lock 完全一致（含由 lock 计算的依赖闭包：防手加 / 手删 / 手改版本）；
3. pyproject 的直接依赖必须已导出到对应产物（防改 pyproject 忘导出）；
4. 本机存在 uv（>= 0.12）时，产物必须与 ``uv export --frozen`` 逐行一致（防手改，离线可跑）。
"""

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
LOCK_FILE = REPO_ROOT / "uv.lock"
RUNTIME_REQUIREMENTS = REPO_ROOT / "requirements.txt"
DEV_REQUIREMENTS = REPO_ROOT / "requirements-dev.txt"

# 产物行形态：包名（可带 extras）== 版本（可带平台 marker）
_REQUIREMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?P<extras>\[[^\]]*\])?==(?P<version>[^;]+)(?P<marker>;.*)?$"
)
# PEP 503 名称规范化
_NORMALIZE_RE = re.compile(r"[-_.]+")
# 导出流程固定的 uv 参数（与 pyproject.toml 头部注释、uv.lock revision 对齐）
_EXPORT_ARGS = ("--no-hashes", "--no-emit-project", "--no-annotate")
_MIN_UV = (0, 12)


def normalize(name: str) -> str:
    return _NORMALIZE_RE.sub("-", name).strip().lower()


def parse_requirements(path: Path) -> dict[str, set[str]]:
    """解析 requirements 产物为 ``包名 → 版本集合``（同名多版本分叉时保留集合）。"""
    parsed: dict[str, set[str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):  # 允许 --index-url 等 pip 选项行（当前导出不含）
            continue
        matched = _REQUIREMENT_RE.match(line)
        assert matched, f"{path.name} 存在非 uv 产物形态的行（请勿手工编辑）：{raw!r}"
        parsed.setdefault(normalize(matched.group("name")), set()).add(matched.group("version").strip())
    return parsed


def load_lock() -> list[dict]:
    return tomllib.loads(LOCK_FILE.read_text(encoding="utf-8"))["package"]


def _index_by_name(packages: list[dict]) -> dict[str, list[dict]]:
    indexed: dict[str, list[dict]] = {}
    for pkg in packages:
        indexed.setdefault(normalize(pkg["name"]), []).append(pkg)
    return indexed


def _edges(pkg: dict, extras: frozenset[str]) -> list[tuple[str, frozenset[str]]]:
    """包节点的依赖边（普通依赖 + 被启用 extra 的依赖）。"""
    edges = [(normalize(dep["name"]), frozenset(dep.get("extra", ()))) for dep in pkg.get("dependencies", [])]
    optionals = pkg.get("optional-dependencies", {})
    for extra in extras:
        edges.extend((normalize(dep["name"]), frozenset(dep.get("extra", ()))) for dep in optionals.get(extra, ()))
    return edges


def dependency_closure(packages: list[dict], roots: list[tuple[str, tuple]]) -> set[str]:
    """由 roots 出发沿 uv.lock 依赖图计算闭包包名集合（平台 marker 统一纳入，与导出产物口径一致）。"""
    indexed = _index_by_name(packages)
    seen: set[tuple[str, frozenset[str]]] = set()
    names: set[str] = set()
    queue: list[tuple[str, frozenset[str]]] = [(normalize(name), frozenset(extras)) for name, extras in roots]
    while queue:
        name, extras = queue.pop()
        if (name, extras) in seen:
            continue
        seen.add((name, extras))
        names.add(name)
        for pkg in indexed.get(name, ()):
            queue.extend(_edges(pkg, extras))
    return names


def project_package(packages: list[dict]) -> dict:
    for pkg in packages:
        if normalize(pkg.get("name", "")) == "xadmin-server":
            return pkg
    raise AssertionError("uv.lock 缺少项目自身条目（xadmin-server），请先执行 uv lock")


def _roots(entries: list[dict]) -> list[tuple[str, tuple]]:
    return [(entry["name"], tuple(entry.get("extra", ()))) for entry in entries]


def test_lock_closure_matches_runtime_requirements():
    """运行时产物 = lock 中运行依赖闭包（防手加 / 手删 / 手改版本）。"""
    packages = load_lock()
    expected = dependency_closure(packages, _roots(project_package(packages).get("dependencies", [])))
    actual = set(parse_requirements(RUNTIME_REQUIREMENTS))
    assert actual == expected, (
        f"requirements.txt 与 uv.lock 运行依赖闭包不一致："
        f"多出 {sorted(actual - expected)}，缺失 {sorted(expected - actual)}（请重新导出，勿手工编辑）"
    )


def test_lock_closure_matches_dev_requirements():
    """开发产物 = lock 中 dev 组闭包。"""
    packages = load_lock()
    dev_entries = project_package(packages).get("dev-dependencies", {}).get("dev", [])
    expected = dependency_closure(packages, _roots(dev_entries))
    actual = set(parse_requirements(DEV_REQUIREMENTS))
    assert actual == expected, (
        f"requirements-dev.txt 与 uv.lock dev 闭包不一致："
        f"多出 {sorted(actual - expected)}，缺失 {sorted(expected - actual)}（请重新导出，勿手工编辑）"
    )


def test_requirement_versions_match_lock():
    """产物的每个 pin 必须在 lock 中同名同版本。"""
    packages = load_lock()
    lock_versions: dict[str, set[str]] = {}
    for pkg in packages:
        lock_versions.setdefault(normalize(pkg["name"]), set()).add(pkg["version"])
    for path in (RUNTIME_REQUIREMENTS, DEV_REQUIREMENTS):
        for name, versions in parse_requirements(path).items():
            assert name in lock_versions, f"{path.name} 的 {name} 未在 uv.lock 中登记（请先 uv lock）"
            for version in versions:
                assert version in lock_versions[name], (
                    f"{path.name} 的 {name}=={version} 与 uv.lock（{sorted(lock_versions[name])}）不一致"
                )


def test_pyproject_direct_dependencies_are_exported():
    """pyproject 直接依赖（含 extras）必须已导出到产物且版本一致。"""
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    runtime = parse_requirements(RUNTIME_REQUIREMENTS)
    dev = parse_requirements(DEV_REQUIREMENTS)

    def _assert_exported(spec: str, exported: dict[str, set[str]], label: str) -> None:
        matched = _REQUIREMENT_RE.match(spec)
        if matched:  # 精确 pin：版本必须一致
            name = normalize(matched.group("name"))
            version = matched.group("version").strip()
            assert name in exported, f"pyproject {label} 依赖 {name} 未出现在导出产物中（请重新导出）"
            assert version in exported[name], (
                f"pyproject {label} 依赖 {name}=={version} 与产物 {sorted(exported[name])} 不一致"
            )
            return
        # 范围声明（如 prometheus-client>=0.21「缺失自动降级」语义）：仅要求已导出，版本由 uv.lock 决定
        name_matched = re.match(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)", spec)
        assert name_matched, f"pyproject {label} 依赖无法解析：{spec!r}"
        name = normalize(name_matched.group("name"))
        assert name in exported, f"pyproject {label} 依赖 {name} 未出现在导出产物中（请重新导出）"

    for spec in pyproject["project"]["dependencies"]:
        _assert_exported(spec, runtime, "运行")
    for spec in pyproject["dependency-groups"]["dev"]:
        _assert_exported(spec, dev, "开发")


def test_optional_dependencies_resolved_in_lock():
    """可选依赖（extras，如 storage）必须已在 uv.lock 中解析，且默认不导出到运行产物。

    可选依赖的语义是「默认不装、按需显式安装」（对象存储），因此既要求声明面与
    lock 同步（防声明了却没锁），也要求它们不出现在默认运行产物中（防悄悄变成必装依赖）。
    """
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    extras = pyproject["project"].get("optional-dependencies", {})
    assert extras, "预期至少声明一个可选依赖组（如 storage）"
    lock_versions: dict[str, set[str]] = {}
    for pkg in load_lock():
        lock_versions.setdefault(normalize(pkg["name"]), set()).add(pkg["version"])
    runtime = parse_requirements(RUNTIME_REQUIREMENTS)

    for extra, specs in extras.items():
        for spec in specs:
            matched = _REQUIREMENT_RE.match(spec)
            name = (
                normalize(matched.group("name"))
                if matched
                else normalize(re.match(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)", spec).group("name"))
            )
            assert name in lock_versions, f"可选依赖 {extra}: {name} 未在 uv.lock 中解析（请先 uv lock）"
            if matched:
                assert matched.group("version").strip() in lock_versions[name], (
                    f"可选依赖 {extra}: {name}=={matched.group('version').strip()} 与 uv.lock"
                    f"（{sorted(lock_versions[name])}）不一致"
                )
            assert name not in runtime, (
                f"可选依赖 {extra}: {name} 出现在默认运行产物 requirements.txt 中（可选依赖应保持「默认不装」语义）"
            )


def test_uv_export_reproduces_requirements_files():
    """本机 uv 可用时：产物必须与 ``uv export --frozen`` 输出逐行一致（防手改）。"""
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("本机未安装 uv，跳过导出可复现性校验（CI 由闭包一致性守护覆盖）")
    version_text = subprocess.run([uv, "--version"], capture_output=True, text=True, timeout=30).stdout
    matched = re.search(r"(\d+)\.(\d+)", version_text)
    if not matched or (int(matched.group(1)), int(matched.group(2))) < _MIN_UV:
        pytest.skip(f"uv 版本低于 0.12（{version_text.strip()}），跳过导出可复现性校验")

    def _content(path: Path) -> list[str]:
        return [line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("#")]

    for target, extra_args in (
        (RUNTIME_REQUIREMENTS, ("--no-group", "dev")),
        (
            DEV_REQUIREMENTS,
            (
                "--only-group",
                "dev",
            ),
        ),
    ):
        result = subprocess.run(
            [uv, "export", "--frozen", *_EXPORT_ARGS, *extra_args, "-o", "-"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"uv export 执行失败：{result.stderr}"
        assert _content(target) == [
            line for line in result.stdout.splitlines() if line.strip() and not line.startswith("#")
        ], f"{target.name} 与 uv export 输出不一致（请勿手工编辑产物，改依赖请改 pyproject.toml 后重新导出）"


# --- 安装路径守护 ---
# 口径：pyproject.toml + uv.lock 是唯一安装依据；requirements*.txt 只服务 pip-audit、
# 手工安装与国产化平台适配，容器与 CI 不得回退到「导出产物 + pip」的安装方式。

DOCKERFILE_BASE = REPO_ROOT / "Dockerfile-base"
DOCKERFILE_DEV = REPO_ROOT / "Dockerfile-dev"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

INSTALL_FROM_EXPORT = "pip install -r requirements"


def _dockerfiles() -> list[Path]:
    return [DOCKERFILE_BASE, DOCKERFILE_DEV]


def _instructions(path: Path) -> str:
    """Dockerfile 去掉注释行后的内容（注释里会解释被禁用的写法，不应参与断言）。"""
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
    )


def test_container_build_installs_via_uv_with_mirror():
    """容器构建必须经 uv 安装导出产物（uv pip + --index-url），不得回退裸 pip、也不得用 --frozen。

    为什么容器不用 ``uv sync --frozen``：``--frozen`` 不重新解析，uv 会直接使用 uv.lock 里固化的
    ``files.pythonhosted.org`` 下载地址，从而绕过 PIP_MIRROR（astral-sh/uv#19625）——国内直连
    官方 CDN 与走镜像源相差一个数量级。改用 ``uv pip install -r requirements*.txt --index-url``
    后，解析与下载都走镜像源；版本可复现性由产物与 uv.lock 的三方守护（本文件其余用例）保证。
    """
    for path in _dockerfiles():
        text = _instructions(path)
        assert "uv pip install" in text, f"{path.name} 未通过 uv 安装依赖（缺少 uv pip install）"
        assert "uv sync --frozen" not in text, (
            f"{path.name} 使用了 uv sync --frozen：它会绕过镜像源直连 files.pythonhosted.org"
            f"（astral-sh/uv#19625），应改用 uv pip install + --index-url"
        )
        assert not re.search(r"(?<!uv )pip install -r requirements", text), (
            f"{path.name} 仍以裸 pip 安装依赖（应经 uv pip install）"
        )


def test_ci_installs_from_lockfile():
    """CI 依赖安装必须以 uv.lock 为准，并显式校验 lock 与 pyproject 同步。"""
    for name in ("test.yml", "perf.yml", "build-image.yml", "lint.yml"):
        text = (WORKFLOW_DIR / name).read_text(encoding="utf-8")
        assert "astral-sh/setup-uv" in text, f"{name} 未安装 uv（astral-sh/setup-uv）"
        assert "uv sync --locked" in text, f"{name} 未以 uv.lock 锁定安装"
        assert INSTALL_FROM_EXPORT not in text, f"{name} 仍以 requirements 产物安装依赖"
    # 容器构建（--frozen）跳过了解析一致性校验，必须由 CI 显式守护 lock ↔ pyproject 同步
    test_yml = (WORKFLOW_DIR / "test.yml").read_text(encoding="utf-8")
    assert "uv lock --check" in test_yml, "test.yml 缺少锁文件一致性校验（uv lock --check）"


def test_uv_version_consistent_in_pyproject_and_dockerfiles():
    """uv 工具链版本同源：pyproject required-version ↔ Dockerfile ARG UV_VERSION。"""
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    required = pyproject["tool"]["uv"]["required-version"]
    matched = re.search(r"(\d+\.\d+\.\d+)", required)
    assert matched, f"pyproject [tool.uv].required-version 无法解析出具体版本：{required!r}"
    expected = matched.group(1)
    for path in _dockerfiles():
        arg = re.search(r"ARG UV_VERSION=([0-9.]+)", path.read_text(encoding="utf-8"))
        assert arg, f"{path.name} 缺少 ARG UV_VERSION（容器内 uv 版本需显式固定）"
        assert arg.group(1) == expected, (
            f"{path.name} 的 UV_VERSION={arg.group(1)} 与 pyproject required-version（{expected}）不一致"
        )


def test_dockerignore_excludes_host_environment():
    """.dockerignore 必须排除宿主虚拟环境与运行期数据（否则宿主平台的包会混进镜像）。"""
    assert DOCKERIGNORE.exists(), "缺少 .dockerignore：宿主 .venv / data / .git 会进入构建上下文与镜像"
    rules = {
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    for required_rule in (".venv", ".git", "data/"):
        assert required_rule in rules, f".dockerignore 缺少排除规则：{required_rule}"
