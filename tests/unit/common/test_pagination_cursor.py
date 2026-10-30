# -*- coding: utf-8 -*-
"""Keyset 游标：手工/旧客户端的不带时区时间戳不得在 PG 上抛 500。"""

import base64
import json
import uuid

import pytest
from rest_framework.exceptions import ValidationError

from common.core.pagination import KeysetPageNumber
from system.models.dict import DataDict

pytestmark = pytest.mark.django_db


def _cursor(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def test_naive_cursor_datetime_is_made_aware():
    pagination = KeysetPageNumber()
    raw = _cursor({"t": "2026-01-01T00:00:00", "pk": str(uuid.uuid4())})
    queryset = pagination._apply_cursor(DataDict.objects.all(), raw)
    assert list(queryset) == []


def test_invalid_cursor_rejected():
    pagination = KeysetPageNumber()
    with pytest.raises(ValidationError):
        pagination._apply_cursor(DataDict.objects.all(), "not-a-cursor")
