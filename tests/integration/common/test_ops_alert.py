# -*- coding: utf-8 -*-
"""A1 运维告警（容器 OOM）：令牌鉴权 / 节流 / 站内信落库 / watcher 脚本接线。

背景：容器 OOM 事件原先只能查 `docker events`（演练登记的观察项），本端点让宿主侧
watcher（utils/oom_alert.sh）能把事件推给站内信/邮件（超管订阅）与出站 Webhook。
"""

import subprocess
from pathlib import Path

import pytest
from django.utils.translation import gettext as _gettext

from common.core.config import SysConfig
from notifications.models import MessageContent

pytestmark = pytest.mark.django_db

ALERT_URL = "/api/common/api/ops-alert"
TOKEN = "ops-alert-token"
PAYLOAD = {
    "source": "container-oom",
    "event": "container oom",
    "host": "docker-host",
    "time": "2026-09-17 18:00:00",
    "detail": "container=xadmin-celery-worker image=xadmin-server id=abc123",
}
SERVER_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def alert_enabled():
    SysConfig.set_value("OPS_ALERT_TOKEN", TOKEN)


class TestOpsAlertAPI:
    def test_token_required(self, api_client, superuser):
        """未配置令牌 → 403；令牌错误 → 403；均不产生告警。"""
        assert api_client.post(ALERT_URL, PAYLOAD, format="json").status_code == 403

        SysConfig.set_value("OPS_ALERT_TOKEN", TOKEN)
        bad = api_client.post(ALERT_URL, PAYLOAD, format="json", HTTP_X_OPS_TOKEN="wrong")
        assert bad.status_code == 403
        assert MessageContent.objects.count() == 0

    def test_publish_and_throttle(self, api_client, superuser, alert_enabled):
        """正确令牌 → 站内信落库（超管可见）+ 同来源同事件 60s 节流。"""
        response = api_client.post(ALERT_URL, PAYLOAD, format="json", HTTP_X_OPS_TOKEN=TOKEN)
        assert response.status_code == 200
        assert response.data["data"]["published"] is True

        # 标题为可翻译文案（本机有 .mo 显中文、CI 无 .mo 显英文）：用 gettext 同源取值断言，
        # 不写死语言（历史教训：locale 相关断言写死语言会在不同环境假红）
        subject = str(_gettext("Ops alert: {}")).format(PAYLOAD["event"])
        message = MessageContent.objects.filter(title=subject).first()
        assert message is not None
        assert message.level == MessageContent.LevelChoices.DANGER
        assert superuser in message.notice_user.all()
        assert "container=xadmin-celery-worker" in message.message

        # 同来源同事件 60s 内再报：节流为 published=false（不产生第二封）
        second = api_client.post(ALERT_URL, PAYLOAD, format="json", HTTP_X_OPS_TOKEN=TOKEN)
        assert second.data["data"]["published"] is False
        assert MessageContent.objects.count() == 1

    def test_different_event_not_throttled(self, api_client, superuser, alert_enabled):
        """同来源不同事件独立计数：OOM 与后续宿主级告警各自送达。"""
        api_client.post(ALERT_URL, PAYLOAD, format="json", HTTP_X_OPS_TOKEN=TOKEN)
        other = dict(PAYLOAD, event="container restart loop")
        assert (
            api_client.post(ALERT_URL, other, format="json", HTTP_X_OPS_TOKEN=TOKEN).data["data"]["published"] is True
        )
        assert MessageContent.objects.count() == 2


class TestOomWatcherScriptWiring:
    def test_script_syntax_and_alert_calls(self):
        """watcher 脚本语法有效，且 oom 监听/回调/重连去重接线完整（无需 docker 即可回归）。"""
        script = SERVER_ROOT / "utils" / "oom_alert.sh"
        subprocess.run(["bash", "-n", str(script)], check=True)
        text = script.read_text(encoding="utf-8")
        assert "--filter event=oom" in text
        assert "send_alert()" in text
        assert "X-Ops-Token: ${OPS_ALERT_TOKEN}" in text
        assert '"source":"container-oom"' in text
        # 断线重连与去重（纳秒游标）能力保留
        assert "--since" in text
        assert "OOM_ALERT_ONCE" in text
