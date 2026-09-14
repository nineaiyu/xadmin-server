# -*- coding: utf-8 -*-
"""企业 IM 通知渠道单测。

覆盖：三家 SDK 客户端（token 获取与缓存 / 请求体形状 / errcode、code 拒绝 /
钉钉 unionId→userid 缓存 / 单用户失败隔离）/ 渠道可达性（凭据缺失降级）/
账号按 flavor 归集（自定义 provider key 命中、无绑定跳过）/ 设置 API（加密
不回显、越权、分渠道测试）。
"""

import pytest
from django.utils.translation import gettext
from django.core.cache import cache

from common.sdk.im.base import ImSdkError
from common.sdk.im.dingtalk import DingTalkClient
from common.sdk.im.feishu import FeishuClient
from common.sdk.im.wecom import WeComClient
from notifications.backends import BACKEND
from notifications.backends.dingtalk import DingTalk
from notifications.backends.feishu import FeiShu
from notifications.backends.wecom import WeCom
from settings.models import Setting
from system.models import SystemConfig, UserOAuthBinding

pytestmark = pytest.mark.django_db

FEISHU_PROVIDER = {
    "key": "my-feishu",
    "name": "飞书",
    "flavor": "feishu",
    "client_id": "cli",
    "client_secret": "sec",
    "enabled": True,
}
DINGTALK_PROVIDER = {
    "key": "custom-ding-key",
    "name": "钉钉",
    "flavor": "dingtalk",
    "client_id": "app-key",
    "client_secret": "app-sec",
    "enabled": True,
}
WECOM_PROVIDER = {
    "key": "wecom",
    "name": "企微",
    "flavor": "wecom",
    "client_id": "corp",
    "client_secret": "corp-sec",
    "enabled": True,
}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class StubHttp:
    """按 URL 片段路由的假 http 客户端，记录全部调用。"""

    def __init__(self, routes=None, error=False):
        self.routes = routes or {}
        self.error = error
        self.calls = []

    def _resolve(self, url):
        if self.error:
            raise RuntimeError("network down")
        for fragment, payload in self.routes.items():
            if fragment in url:
                return FakeResponse(payload)
        return FakeResponse({})

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("get", url, params, headers))
        return self._resolve(url)

    def post(self, url, json=None, params=None, headers=None, timeout=None):
        self.calls.append(("post", url, json, params))
        return self._resolve(url)


@pytest.fixture(autouse=True)
def _clear_im_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def im_providers():
    """三家 flavor provider 配置（钉钉用自定义 key，验证按 flavor 归集）。"""
    SystemConfig.objects.update_or_create(
        key="OAUTH_PROVIDERS",
        defaults={"value": [FEISHU_PROVIDER, DINGTALK_PROVIDER, WECOM_PROVIDER], "is_active": True},
    )
    cache.clear()
    yield
    SystemConfig.objects.filter(key="OAUTH_PROVIDERS").delete()
    cache.clear()


def enable_all_channels(settings_fixture):
    settings_fixture.DINGTALK_ENABLED = True
    settings_fixture.DINGTALK_APP_KEY = "app-key"
    settings_fixture.DINGTALK_APP_SECRET = "app-sec"
    settings_fixture.DINGTALK_AGENT_ID = "agent-1"
    settings_fixture.WECOM_ENABLED = True
    settings_fixture.WECOM_CORP_ID = "corp"
    settings_fixture.WECOM_CORP_SECRET = "corp-sec"
    settings_fixture.WECOM_AGENT_ID = "1000002"
    settings_fixture.FEISHU_ENABLED = True
    settings_fixture.FEISHU_APP_ID = "cli"
    settings_fixture.FEISHU_APP_SECRET = "sec"


# ---------------------------------------------------------------- SDK 客户端


class TestDingTalkClient:
    def test_token_fetch_and_cache(self):
        http = StubHttp({"gettoken": {"errcode": 0, "access_token": "dt-tok"}})
        client = DingTalkClient({"app_key": "k", "app_secret": "s", "agent_id": "a"}, http_client=http)
        assert client._cached_token() == "dt-tok"
        client._cached_token()  # 命中缓存
        assert sum(1 for c in http.calls if "gettoken" in c[1]) == 1
        token_call = next(c for c in http.calls if "gettoken" in c[1])
        assert token_call[2] == {"appkey": "k", "appsecret": "s"}

    def test_token_cache_key_follows_credentials(self):
        """token 缓存 key 含凭据摘要：同凭据命中缓存，换凭据必须重新获取。

        回归守护：曾因 `_cached_token({})` 摘要恒为空 → 所有配置共用一条缓存，
        改密钥仍沿用旧 token，测试接口也会「秒过」掩盖配置错误。
        """
        http = StubHttp({"gettoken": {"errcode": 0, "access_token": "dt-tok"}})
        DingTalkClient({"app_key": "k1", "app_secret": "s1", "agent_id": "a"}, http_client=http)._cached_token()
        # 同凭据新实例：命中缓存
        DingTalkClient({"app_key": "k1", "app_secret": "s1", "agent_id": "a"}, http_client=http)._cached_token()
        assert sum(1 for c in http.calls if "gettoken" in c[1]) == 1
        # 换 AppKey：摘要变化 → 重新获取
        DingTalkClient({"app_key": "k2", "app_secret": "s1", "agent_id": "a"}, http_client=http)._cached_token()
        assert sum(1 for c in http.calls if "gettoken" in c[1]) == 2

    def test_token_error_rejected(self):
        http = StubHttp({"gettoken": {"errcode": 40001, "errmsg": "invalid appkey"}})
        client = DingTalkClient({"app_key": "k", "app_secret": "s", "agent_id": "a"}, http_client=http)
        with pytest.raises(ImSdkError):
            client._cached_token()

    def test_unionid_to_userid_cached(self):
        http = StubHttp(
            {
                "gettoken": {"errcode": 0, "access_token": "dt-tok"},
                "getbyunionid": {"errcode": 0, "result": {"userid": "staff-1"}},
            }
        )
        client = DingTalkClient({"app_key": "k", "app_secret": "s", "agent_id": "a"}, http_client=http)
        assert client.get_userid_by_unionid("union-x") == "staff-1"
        assert client.get_userid_by_unionid("union-x") == "staff-1"  # 缓存命中
        assert sum(1 for c in http.calls if "getbyunionid" in c[1]) == 1

    def test_unionid_cache_isolated_by_credentials(self):
        """userid 缓存 key 含凭据摘要：换企业/换应用不串号（unionId → 各 corp 下 userid 不同）。"""
        http = StubHttp(
            {
                "gettoken": {"errcode": 0, "access_token": "dt-tok"},
                "getbyunionid": {"errcode": 0, "result": {"userid": "staff-1"}},
            }
        )
        DingTalkClient({"app_key": "k1", "app_secret": "s", "agent_id": "a"}, http_client=http).get_userid_by_unionid(
            "union-x"
        )
        DingTalkClient({"app_key": "k2", "app_secret": "s", "agent_id": "a"}, http_client=http).get_userid_by_unionid(
            "union-x"
        )
        assert sum(1 for c in http.calls if "getbyunionid" in c[1]) == 2

    def test_send_text_body(self):
        http = StubHttp(
            {
                "gettoken": {"errcode": 0, "access_token": "dt-tok"},
                "asyncsend_v2": {"errcode": 0, "task_id": 111},
            }
        )
        client = DingTalkClient({"app_key": "k", "app_secret": "s", "agent_id": "agent-9"}, http_client=http)
        client.send_text(["u1", "u2"], "hello")
        send_call = next(c for c in http.calls if "asyncsend_v2" in c[1])
        assert send_call[2] == {
            "agent_id": "agent-9",
            "userid_list": "u1|u2",
            "msg": {"msgtype": "text", "text": {"content": "hello"}},
        }

    def test_send_rejected(self):
        http = StubHttp(
            {
                "gettoken": {"errcode": 0, "access_token": "dt-tok"},
                "asyncsend_v2": {"errcode": 60011, "errmsg": "no privilege"},
            }
        )
        client = DingTalkClient({"app_key": "k", "app_secret": "s", "agent_id": "a"}, http_client=http)
        with pytest.raises(ImSdkError):
            client.send_text(["u1"], "hello")


class TestWeComClient:
    def test_send_body_and_token_params(self):
        http = StubHttp(
            {
                "gettoken": {"errcode": 0, "access_token": "wc-tok"},
                "message/send": {"errcode": 0, "invaliduser": ""},
            }
        )
        client = WeComClient({"corp_id": "corp", "corp_secret": "sec", "agent_id": "1000002"}, http_client=http)
        client.send_text(["zhangsan", "lisi"], "hello")
        token_call = next(c for c in http.calls if "gettoken" in c[1])
        assert token_call[2] == {"corpid": "corp", "corpsecret": "sec"}
        send_call = next(c for c in http.calls if "message/send" in c[1])
        assert send_call[3] == {"access_token": "wc-tok"}
        assert send_call[2] == {
            "touser": "zhangsan|lisi",
            "msgtype": "text",
            "agentid": "1000002",
            "text": {"content": "hello"},
        }

    def test_errcode_rejected(self):
        http = StubHttp(
            {
                "gettoken": {"errcode": 0, "access_token": "wc-tok"},
                "message/send": {"errcode": 81013, "errmsg": "user not found"},
            }
        )
        client = WeComClient({"corp_id": "c", "corp_secret": "s", "agent_id": "1"}, http_client=http)
        with pytest.raises(ImSdkError):
            client.send_text(["u1"], "hello")


class TestFeishuClient:
    def test_send_per_user_with_union_id(self):
        http = StubHttp(
            {
                "tenant_access_token/internal": {"code": 0, "tenant_access_token": "fs-tok", "expire": 7200},
                "im/v1/messages": {"code": 0},
            }
        )
        client = FeishuClient({"app_id": "cli", "app_secret": "sec"}, http_client=http)
        client.send_text(["un-1", "un-2"], "hello")
        token_call = next(c for c in http.calls if "tenant_access_token" in c[1])
        assert token_call[2] == {"app_id": "cli", "app_secret": "sec"}
        sends = [c for c in http.calls if "im/v1/messages" in c[1]]
        assert len(sends) == 2
        assert sends[0][3] == {"receive_id_type": "union_id"}
        import json

        assert json.loads(sends[0][2]["content"]) == {"text": "hello"}

    def test_single_user_failure_isolated(self):
        """第一个收件人失败（不可见范围）不影响第二个。"""
        http = StubHttp(
            {
                "tenant_access_token/internal": {"code": 0, "tenant_access_token": "fs-tok"},
                "im/v1/messages": {"code": 99992403, "msg": "no permission"},
            }
        )
        client = FeishuClient({"app_id": "c", "app_secret": "s"}, http_client=http)
        # 全部失败但不抛出（渠道级隔离在 Message.send_msg，用户级在此吞掉）
        client.send_text(["un-1", "un-2"], "hello")


# ---------------------------------------------------------------- 渠道可达性


class TestChannelEnable:
    def test_disabled_by_default(self):
        assert DingTalk.is_enable() is False
        assert WeCom.is_enable() is False
        assert FeiShu.is_enable() is False

    def test_enabled_requires_credentials(self, settings):
        settings.FEISHU_ENABLED = True
        assert FeiShu.is_enable() is False  # 缺凭据降级
        settings.FEISHU_APP_ID = "cli"
        settings.FEISHU_APP_SECRET = "sec"
        assert FeiShu.is_enable() is True

    def test_backend_registry_contains_im_channels(self):
        assert {b for b in BACKEND} >= {BACKEND.DINGTALK, BACKEND.WECOM, BACKEND.FEISHU}


# ---------------------------------------------------------------- 账号派生与发送


class TestBindingAccounts:
    def test_accounts_by_flavor_with_custom_key(self, im_providers, normal_user, superuser):
        """自定义 provider key（custom-ding-key）仍按 flavor 命中；无绑定用户跳过。"""
        UserOAuthBinding.objects.create(user=normal_user, provider="custom-ding-key", subject="union-n1")
        backend = DingTalk()
        accounts, unbound, __ = backend.get_accounts([normal_user, superuser])
        assert [(subject, user.pk) for subject, user in accounts] == [("union-n1", normal_user.pk)]
        assert [user.pk for user in unbound] == [superuser.pk]

    def test_send_dingtalk_converts_unionid(self, settings, im_providers, normal_user, monkeypatch):
        enable_all_channels(settings)
        UserOAuthBinding.objects.create(user=normal_user, provider="custom-ding-key", subject="union-n1")
        http = StubHttp(
            {
                "gettoken": {"errcode": 0, "access_token": "dt-tok"},
                "getbyunionid": {"errcode": 0, "result": {"userid": "staff-n1"}},
                "asyncsend_v2": {"errcode": 0, "task_id": 1},
            }
        )
        from common.sdk.im import dingtalk as dingtalk_sdk

        monkeypatch.setattr(
            dingtalk_sdk,
            "DingTalkClient",
            lambda credentials, http_client=None: DingTalkClient(credentials, http_client=http),
        )
        DingTalk().send_msg([normal_user], "正文", subject="标题")
        send_call = next(c for c in http.calls if "asyncsend_v2" in c[1])
        assert send_call[2]["userid_list"] == "staff-n1"
        assert send_call[2]["msg"]["text"]["content"] == "标题\n正文"

    def test_send_wecom_direct_userid(self, settings, im_providers, normal_user, monkeypatch):
        enable_all_channels(settings)
        UserOAuthBinding.objects.create(user=normal_user, provider="wecom", subject="zhangsan")
        http = StubHttp(
            {
                "gettoken": {"errcode": 0, "access_token": "wc-tok"},
                "message/send": {"errcode": 0},
            }
        )
        from common.sdk.im import wecom as wecom_sdk

        monkeypatch.setattr(
            wecom_sdk, "WeComClient", lambda credentials, http_client=None: WeComClient(credentials, http_client=http)
        )
        WeCom().send_msg([normal_user], "正文", subject="标题")
        send_call = next(c for c in http.calls if "message/send" in c[1])
        assert send_call[2]["touser"] == "zhangsan"

    def test_send_skipped_when_no_binding(self, settings, im_providers, normal_user, monkeypatch):
        enable_all_channels(settings)
        http = StubHttp({})
        from common.sdk.im import wecom as wecom_sdk

        monkeypatch.setattr(
            wecom_sdk, "WeComClient", lambda credentials, http_client=None: WeComClient(credentials, http_client=http)
        )
        WeCom().send_msg([normal_user], "正文")  # 无绑定：不产生任何 http 调用
        assert http.calls == []

    def test_send_feishu_union_id(self, settings, im_providers, normal_user, monkeypatch):
        enable_all_channels(settings)
        UserOAuthBinding.objects.create(user=normal_user, provider="my-feishu", subject="un-n1")
        http = StubHttp(
            {
                "tenant_access_token/internal": {"code": 0, "tenant_access_token": "fs-tok"},
                "im/v1/messages": {"code": 0},
            }
        )
        from common.sdk.im import feishu as feishu_sdk

        monkeypatch.setattr(
            feishu_sdk,
            "FeishuClient",
            lambda credentials, http_client=None: FeishuClient(credentials, http_client=http),
        )
        FeiShu().send_msg([normal_user], "正文")
        send_call = next(c for c in http.calls if "im/v1/messages" in c[1])
        assert send_call[2]["receive_id"] == "un-n1"


# ---------------------------------------------------------------- 设置 API


class TestNotifyImSettingsApi:
    URL = "/api/settings/notify/im"

    def test_anonymous_rejected(self, api_client):
        assert api_client.get(self.URL).status_code == 401

    def test_normal_user_rejected(self, api_client, normal_user):
        api_client.force_authenticate(user=normal_user)
        assert api_client.get(self.URL).status_code == 403

    def test_retrieve_masks_secrets(self, auth_client):
        body = auth_client.get(self.URL).json()
        data = body["data"]
        assert "DINGTALK_APP_SECRET" not in data
        assert "FEISHU_APP_SECRET" not in data
        assert data["DINGTALK_ENABLED"] is False

    def test_partial_update_encrypts_secret(self, auth_client):
        payload = {
            "FEISHU_ENABLED": True,
            "FEISHU_APP_ID": "cli",
            "FEISHU_APP_SECRET": "super-secret",
        }
        response = auth_client.patch(self.URL, payload, format="json")
        assert response.status_code == 200, response.data
        row = __import__("settings.models", fromlist=["Setting"]).Setting.objects.get(name="FEISHU_APP_SECRET")
        assert row.encrypted is True
        assert "super-secret" not in (row.value or "")
        assert Setting.objects.get(name="FEISHU_APP_ID").cleaned_value == "cli"

    def test_connection_test_reports_per_channel(self, auth_client, monkeypatch):
        """已启用 + 凭据齐 → OK；未启用 → Disabled；缺失 → 缺配置提示。"""
        from unittest import mock

        payload = {
            "FEISHU_ENABLED": True,
            "FEISHU_APP_ID": "cli",
            "FEISHU_APP_SECRET": "sec",
            "WECOM_ENABLED": False,
            # 钉钉启用但缺 AppKey：命中「缺配置」分支
            "DINGTALK_ENABLED": True,
            "DINGTALK_APP_SECRET": "sec",
            "DINGTALK_AGENT_ID": "agent-1",
        }
        with (
            mock.patch.object(FeishuClient, "_cached_token", return_value="tok"),
            mock.patch.object(DingTalkClient, "_cached_token", return_value="tok"),
            mock.patch.object(WeComClient, "_cached_token", return_value="tok"),
        ):
            body = auth_client.post(self.URL, payload, format="json").json()
        data = body["data"]
        disabled, ok = str(gettext("Disabled")), str(gettext("OK"))
        assert data["FeiShu"] == ok
        assert data["WeCom"] == disabled
        assert str(gettext("Missing configuration: {}")).format("DINGTALK_APP_KEY") == data["DingTalk"]

    def test_connection_test_failure_isolated(self, auth_client):
        """某渠道 token 失败给出可读错误，其余渠道照常判定。"""
        from unittest import mock

        payload = {"FEISHU_ENABLED": True, "FEISHU_APP_ID": "cli", "FEISHU_APP_SECRET": "bad"}
        with mock.patch.object(FeishuClient, "_cached_token", side_effect=ImSdkError("feishu rejected: code=10014")):
            body = auth_client.post(self.URL, payload, format="json").json()
        assert body["code"] == 1002
        assert "feishu rejected" in body["data"]["FeiShu"]

    def test_connection_test_single_channel(self, auth_client):
        """渠道级测试（?channel=feishu）：只测指定渠道，其他渠道的缺失不影响判定。"""
        from unittest import mock

        payload = {
            "FEISHU_ENABLED": True,
            "FEISHU_APP_ID": "cli",
            "FEISHU_APP_SECRET": "sec",
            # 钉钉启用但缺 AppKey：全量测试会失败，渠道级测试不应被牵连
            "DINGTALK_ENABLED": True,
        }
        with mock.patch.object(FeishuClient, "_cached_token", return_value="tok"):
            body = auth_client.post(f"{self.URL}?channel=feishu", payload, format="json").json()
        assert body["code"] == 1000
        assert list(body["data"]) == ["FeiShu"]
        assert body["detail"] == str(gettext("Test completed"))

    def test_connection_test_single_channel_failure_detail(self, auth_client):
        """单渠道失败：detail 直接携带该渠道错误（页签内可见，不再是笼统的测试完成）。"""
        from unittest import mock

        payload = {"FEISHU_ENABLED": True, "FEISHU_APP_ID": "cli", "FEISHU_APP_SECRET": "bad"}
        with mock.patch.object(FeishuClient, "_cached_token", side_effect=ImSdkError("feishu rejected: code=10014")):
            body = auth_client.post(f"{self.URL}?channel=feishu", payload, format="json").json()
        assert body["code"] == 1002
        assert "feishu rejected" in body["detail"]

    def test_connection_test_unknown_channel_rejected(self, auth_client):
        """未知渠道参数：显式 400，不静默退化成全量测试。"""
        response = auth_client.post(f"{self.URL}?channel=slack", {}, format="json")
        assert response.status_code == 400

    def test_channel_scope_retrieve_only_own_fields(self, auth_client):
        """?channel= 作用域：retrieve 只回本渠道字段（设置页三页签各自独立的数据源）。"""
        data = auth_client.get(f"{self.URL}?channel=wecom").json()["data"]
        # write_only 密文不回显，只回配置项与密文以外的字段
        assert set(data) == {"WECOM_ENABLED", "WECOM_CORP_ID", "WECOM_AGENT_ID"}

    def test_channel_scope_search_columns_required(self, auth_client):
        """?channel= 作用域：search-columns 只下发本渠道字段，非密文字段 required=True
        （前端据此渲染必填标记并拦截空值）。"""
        columns = auth_client.get(f"{self.URL}/search-columns?channel=dingtalk").json()["data"]
        by_key = {item["key"]: item for item in columns}
        assert set(by_key) == {"DINGTALK_ENABLED", "DINGTALK_APP_KEY", "DINGTALK_APP_SECRET", "DINGTALK_AGENT_ID"}
        assert by_key["DINGTALK_APP_KEY"]["required"] is True
        assert by_key["DINGTALK_AGENT_ID"]["required"] is True
        # write_only 密文回显为空，必填会与「不回显」死锁（沿用邮件密码口径）
        assert by_key["DINGTALK_APP_SECRET"]["required"] is False

    def test_connection_test_missing_required_rejected(self, auth_client):
        """渠道必填校验：缺 AppKey 时测试请求直接 400，而非静默通过或笼统报错。"""
        response = auth_client.post(f"{self.URL}?channel=dingtalk", {"DINGTALK_AGENT_ID": "a"}, format="json")
        assert response.status_code == 400
        assert "DINGTALK_APP_KEY" in response.data

    def test_connection_test_disabled_channel_reports_failure(self, auth_client, settings):
        """未启用渠道：明确反馈「渠道未启用」，不再报测试完成。"""
        settings.DINGTALK_ENABLED = False
        payload = {"DINGTALK_APP_KEY": "k", "DINGTALK_AGENT_ID": "a"}
        body = auth_client.post(f"{self.URL}?channel=dingtalk", payload, format="json").json()
        assert body["code"] == 1002
        assert body["detail"] == str(gettext("Channel not enabled"))

    def test_connection_test_all_disabled_reports_failure(self, auth_client, settings):
        """全量模式且三家都未启用：同样按失败反馈（曾把「什么都没配」报成测试完成）。"""
        settings.DINGTALK_ENABLED = False
        settings.WECOM_ENABLED = False
        settings.FEISHU_ENABLED = False
        body = auth_client.post(self.URL, {}, format="json").json()
        assert body["code"] == 1002
        assert body["detail"] == str(gettext("Channel not enabled"))
        assert set(body["data"].values()) == {str(gettext("Disabled"))}
