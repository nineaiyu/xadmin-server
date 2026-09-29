# -*- coding: utf-8 -*-
"""安装器镜像策略（P1-38）：离线镜像映射完整性与季度核对流程。

跨仓测试：`xadmin-installer` 是同工作区兄弟目录；**单仓检出时跳过**
（CI 只检出 server 时不能因此失败——与 test_csp.py / test_module_remove 同口径）。
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
INSTALLER = Path(os.environ.get("XADMIN_INSTALLER_DIR", ROOT.parent / "xadmin-installer"))

pytestmark = pytest.mark.skipif(
    not (INSTALLER / "compose").is_dir(),
    reason="xadmin-installer 不在工作区（单仓检出）",
)


def _compose_third_party_images() -> dict:
    """compose 里固定的第三方镜像（自有镜像走 ${VERSION}，不在此列）。"""
    images = {}
    for path in sorted((INSTALLER / "compose").glob("*.yml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "image:" not in line or "${VERSION}" in line:
                continue
            image = line.split("image:", 1)[1].strip()
            if image:
                images[image] = path.name
    return images


def _mapping() -> dict:
    mapping = {}
    for line in (INSTALLER / "utils" / "base-images.yml").read_text(encoding="utf-8").splitlines():
        matched = re.match(r'^"([^"]+)":\s*"?([^"\s]+)"?', line.strip())
        if matched:
            mapping[matched.group(1)] = matched.group(2)
    return mapping


class TestImageMapping:
    def test_every_compose_image_has_offline_mapping(self):
        """缺映射 = 离线安装拉不到镜像（季度核对流程第 3 步同一口径）。"""
        images = _compose_third_party_images()
        mapping = _mapping()
        assert images, "installer compose 未发现第三方镜像，路径或格式已变"
        missing = {f"docker.io/library/{image}": source for image, source in images.items()}
        missing = {key: value for key, value in missing.items() if key not in mapping}
        assert not missing, f"以下镜像缺少 utils/base-images.yml 映射：{missing}"

    def test_mapping_targets_pinned_tags(self):
        """映射的离线镜像必须带 tag（浮动 tag 会让离线安装不可复现）。"""
        for upstream, target in _mapping().items():
            assert re.search(r":[^/]+$", target), f"{upstream} 的映射缺少 tag：{target}"


class TestCheckScript:
    def test_script_exists_and_parses(self):
        script = INSTALLER / "scripts" / "check_images.sh"
        assert script.is_file()
        assert shutil.which("bash") is not None, "需要 bash 做语法检查"
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr

    def test_check_mapping_mode_passes(self):
        script = INSTALLER / "scripts" / "check_images.sh"
        result = subprocess.run(["bash", str(script), "--check-mapping"], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        assert "镜像映射完整" in result.stdout

    def test_table_mode_lists_all_images(self):
        script = INSTALLER / "scripts" / "check_images.sh"
        result = subprocess.run(["bash", str(script)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        for image in _compose_third_party_images():
            assert image in result.stdout


class TestProcedureDocumented:
    def test_readme_documents_quarterly_review(self):
        readme = (INSTALLER / "README.md").read_text(encoding="utf-8")
        for token in ("季度镜像核对", "scripts/check_images.sh", "--check-mapping", "utils/base-images.yml"):
            assert token in readme, token
