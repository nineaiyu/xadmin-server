# -*- coding: utf-8 -*-
"""数据集模型/字段白名单：未登记即拒绝（fail-closed）。"""

import pytest
from django.core.exceptions import ValidationError

from dataset.utils.dataset import available_fields, available_models, get_whitelisted_model
from system.models import ModelLabelField

pytestmark = pytest.mark.django_db

MODEL = "identity.userinfo"


@pytest.fixture
def registered_model():
    root, _ = ModelLabelField.objects.get_or_create(
        name=MODEL,
        defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "用户"},
    )
    ModelLabelField.objects.get_or_create(
        name="username",
        parent=root,
        defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "用户名"},
    )
    yield
    root.delete()


class TestDatasetModelWhitelist:
    def test_unregistered_model_is_rejected(self):
        ModelLabelField.objects.filter(name=MODEL, parent__isnull=True).delete()
        assert MODEL not in available_models()
        with pytest.raises(ValidationError, match=MODEL):
            get_whitelisted_model(MODEL)

    def test_registered_model_only_exposes_whitelisted_fields(self, registered_model):
        assert MODEL in available_models()
        assert available_fields(MODEL) == ["username"]
        assert get_whitelisted_model(MODEL) is not None

    def test_unknown_field_is_not_in_whitelist(self, registered_model):
        assert "password" not in available_fields(MODEL)
