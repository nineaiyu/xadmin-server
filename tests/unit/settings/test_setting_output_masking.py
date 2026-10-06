# -*- coding: utf-8 -*-
"""Setting 总表 list/export 敏感值脱敏与掩码回写守护测试。

锁定 SettingSerializer 的治理口径：敏感行（加密行 / 声明加密键）的 value 在
list、detail、export 文件中一律掩码，非敏感行原样返回；写入侧掩码占位值
不覆盖库内原值、新建不落占位串，真实新值照常写入。
"""

import csv
import io
import json

import pytest

from common.base.utils import signer
from common.core.credentials import MASK
from settings.models import Setting

pytestmark = pytest.mark.django_db

SETTING_URL = "/api/settings/setting"


def _make_rows():
    """三类行：加密敏感行 / 声明敏感的存量明文行 / 非敏感普通行。"""
    encrypted = Setting.objects.create(
        name="EMAIL_HOST_PASSWORD",
        value=signer.encrypt(json.dumps("smtp-secret").encode("utf-8")).decode("utf-8"),
        encrypted=True,
        category="email",
    )
    plaintext = Setting.objects.create(name="AI_API_KEY", value="sk-legacy-plaintext", encrypted=False, category="ai")
    normal = Setting.objects.create(
        name="SITE_URL", value="https://mask.example.com", encrypted=False, category="basic"
    )
    return encrypted, plaintext, normal


def _row_by_name(resp, name):
    results = resp.data["data"]["results"]
    return next(row for row in results if row["name"] == name)


def _csv_rows(resp) -> list[list[str]]:
    lines = resp.content.decode("utf-8-sig").strip().splitlines()
    return [row for row in csv.reader(io.StringIO("\n".join(lines))) if row]


def _csv_column_index(header_row, field_name) -> int:
    """列头格式为「label(field_name)」，label 随 locale 变化，按括号内字段名定位。"""
    for index, cell in enumerate(header_row):
        if cell.endswith(f"({field_name})"):
            return index
    raise AssertionError(f"column {field_name} not in header: {header_row}")


class TestOutputMasking:
    def test_list_masks_sensitive_rows_and_keeps_others(self, auth_client):
        encrypted, plaintext, normal = _make_rows()
        resp = auth_client.get(SETTING_URL)
        assert resp.data["code"] == 1000, resp.data

        assert _row_by_name(resp, "EMAIL_HOST_PASSWORD")["value"] == MASK
        assert _row_by_name(resp, "AI_API_KEY")["value"] == MASK
        assert _row_by_name(resp, "SITE_URL")["value"] == "https://mask.example.com"
        # 掩码不改变行元数据，加密标记照常下发
        assert _row_by_name(resp, "EMAIL_HOST_PASSWORD")["encrypted"] is True

    def test_detail_masks_sensitive_row_only(self, auth_client):
        encrypted, plaintext, normal = _make_rows()
        resp = auth_client.get(f"{SETTING_URL}/{encrypted.pk}")
        assert resp.data["data"]["value"] == MASK

        resp = auth_client.get(f"{SETTING_URL}/{normal.pk}")
        assert resp.data["data"]["value"] == "https://mask.example.com"

    def test_export_csv_masks_sensitive_values(self, auth_client):
        encrypted, plaintext, normal = _make_rows()
        resp = auth_client.get(f"{SETTING_URL}/export-data?type=csv")
        assert resp.status_code == 200, resp.data

        rows = _csv_rows(resp)
        name_idx = _csv_column_index(rows[0], "name")
        value_idx = _csv_column_index(rows[0], "value")
        values = {row[name_idx]: row[value_idx] for row in rows[1:]}
        assert values["EMAIL_HOST_PASSWORD"] == MASK
        assert values["AI_API_KEY"] == MASK
        assert values["SITE_URL"] == "https://mask.example.com"


class TestMaskWritebackGuard:
    def test_patch_mask_keeps_stored_secret(self, auth_client):
        encrypted, _, _ = _make_rows()
        resp = auth_client.patch(f"{SETTING_URL}/{encrypted.pk}", {"value": MASK}, format="json")
        assert resp.data["code"] == 1000, resp.data

        encrypted.refresh_from_db()
        assert encrypted.cleaned_value == "smtp-secret", "掩码占位提交不得覆盖库内真实值"

    def test_patch_real_new_value_passes_guard_but_reads_masked(self, auth_client):
        """真实新值不受守护拦截（原样落列）；读侧仍按敏感行掩码。"""
        _, plaintext, _ = _make_rows()
        resp = auth_client.patch(f"{SETTING_URL}/{plaintext.pk}", {"value": "sk-rotated"}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["value"] == MASK

        plaintext.refresh_from_db()
        assert plaintext.value == "sk-rotated"

    def test_create_with_mask_stores_unconfigured(self, auth_client):
        resp = auth_client.post(
            SETTING_URL,
            {"name": "LDAP_BIND_PASSWORD", "value": MASK, "encrypted": True, "category": "ldap"},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data

        row = Setting.objects.get(name="LDAP_BIND_PASSWORD")
        assert row.value != MASK, "掩码占位串不得落库"
        assert row.cleaned_value is None

    def test_non_sensitive_row_write_unaffected(self, auth_client):
        """非敏感行的读-改-回写链路保持原状。"""
        _, _, normal = _make_rows()
        resp = auth_client.patch(f"{SETTING_URL}/{normal.pk}", {"value": "https://new.example.com"}, format="json")
        assert resp.data["code"] == 1000
        assert resp.data["data"]["value"] == "https://new.example.com"

        normal.refresh_from_db()
        assert normal.value == "https://new.example.com"


class TestValueSearchSideChannel:
    """按 value 子串搜索激活时排除敏感行：命中/不命中不得构成对敏感值的探测预言机。"""

    def _search(self, auth_client, query):
        resp = auth_client.get(SETTING_URL, {"value": query})
        assert resp.data["code"] == 1000, resp.data
        return [row["name"] for row in resp.data["data"]["results"]]

    def test_secret_substring_never_matches_sensitive_rows(self, auth_client):
        """敏感值子串（无论加密行还是声明键的存量明文行）不出现在搜索结果。"""
        _make_rows()
        assert self._search(auth_client, "smtp-secret") == []
        assert self._search(auth_client, "sk-legacy") == []
        # 前缀式逐字符探测同样不命中
        assert self._search(auth_client, "sk-") == []
        assert self._search(auth_client, "smtp") == []

    def test_normal_value_still_matches(self, auth_client):
        """非敏感行搜索行为不变。"""
        _, _, _ = _make_rows()
        assert "SITE_URL" in self._search(auth_client, "mask.example")
        assert self._search(auth_client, "不存在的子串") == []

    def test_search_by_name_filter_unaffected(self, auth_client):
        """按 name 过滤不受影响（敏感行按名定位走掩码读取口径）。"""
        _make_rows()
        resp = auth_client.get(SETTING_URL, {"name": "AI_API_KEY"})
        assert resp.data["code"] == 1000, resp.data
        names = [row["name"] for row in resp.data["data"]["results"]]
        assert names == ["AI_API_KEY"]
