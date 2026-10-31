# -*- coding: utf-8 -*-
"""凭证撤销链路守护：改密/重置、应用范围收紧必须同时撤销 OAuth refresh。"""

import pytest
from django.utils import timezone

from identity.models.token import ApiApplication, OAuthRefreshToken, PersonalAccessToken
from identity.services.token_issue import revoke_application_tokens
from identity.utils.session import revoke_user_credentials

pytestmark = pytest.mark.django_db


def _application(creator, scopes=None):
    return ApiApplication.objects.create(
        name="凭证撤销测试应用",
        client_id=f"app_{creator.pk}",
        client_secret_hash="a" * 64,
        client_secret_prefix="aps_00000000",
        scopes=scopes or [],
        creator=creator,
    )


def _refresh_row(application, user):
    return OAuthRefreshToken.objects.create(
        token_hash=f"{application.pk}{user.pk}"[:64],
        application=application,
        user=user,
        scopes=list(application.scopes or []),
        creator=user,
    )


def test_revoke_user_credentials_revokes_oauth_refresh(normal_user):
    """改密/重置只撤 access 不够：refresh 仍可换发新 access，30 天内等于没改密。"""
    application = _application(normal_user)
    row = _refresh_row(application, normal_user)
    PersonalAccessToken.objects.create(
        name="oauth-access",
        token_hash="b" * 64,
        token_prefix="aoat_0000000",
        api_application=application,
        creator=normal_user,
    )

    result = revoke_user_credentials(normal_user.pk)

    row.refresh_from_db()
    assert row.is_revoked is True
    assert result["oauth_refresh"] == 1
    assert result["pat"] >= 1


def test_scope_tightening_revokes_oauth_refresh(normal_user):
    """应用 scope 收紧：refresh 行同步撤销，旧 scope 不能续命。"""
    application = _application(normal_user, scopes=["api/identity/userinfo"])
    row = _refresh_row(application, normal_user)

    revoke_application_tokens(application)

    row.refresh_from_db()
    assert row.is_revoked is True
    assert row.updated_time <= timezone.now()
