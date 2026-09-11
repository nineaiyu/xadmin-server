# -*- coding: utf-8 -*-
"""S2 备份失败告警：令牌鉴权 / 节流 / 站内信落库 / 备份脚本语法与告警接线。

背景：备份/异地同步失败原先只有 WARN 日志（deployment.md 检查清单遗留项），
本端点让 db-backup 容器能把失败推给站内信/邮件（超管订阅）。
"""

import subprocess
from pathlib import Path

import pytest
from django.utils.translation import gettext as _gettext

from common.core.config import SysConfig
from notifications.models import MessageContent

pytestmark = pytest.mark.django_db

ALERT_URL = "/api/common/api/backup-alert"
TOKEN = "backup-alert-token"
PAYLOAD = {
    "source": "db-backup",
    "event": "pg_dump failed",
    "host": "db-backup",
    "time": "2026-09-11 03:00:00",
    "detail": "database=xadmin host=postgresql:5432",
}
SERVER_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def alert_enabled():
    SysConfig.set_value("BACKUP_ALERT_TOKEN", TOKEN)


class TestBackupAlertAPI:
    def test_token_required(self, api_client, superuser):
        """未配置令牌 → 403；令牌错误 → 403；均不产生告警。"""
        assert api_client.post(ALERT_URL, PAYLOAD, format="json").status_code == 403

        SysConfig.set_value("BACKUP_ALERT_TOKEN", TOKEN)
        bad = api_client.post(ALERT_URL, PAYLOAD, format="json", HTTP_X_BACKUP_TOKEN="wrong")
        assert bad.status_code == 403
        assert MessageContent.objects.count() == 0

    def test_publish_and_throttle(self, api_client, superuser, alert_enabled):
        """正确令牌 → 站内信落库（超管可见）+ 同源 60s 节流。"""
        response = api_client.post(ALERT_URL, PAYLOAD, format="json", HTTP_X_BACKUP_TOKEN=TOKEN)
        assert response.status_code == 200
        assert response.data["data"]["published"] is True

        # 标题为可翻译文案（本机有 .mo 显中文、CI 无 .mo 显英文）：用 gettext 同源取值断言，
        # 不写死英文（历史教训：locale 相关断言写死语言会在不同环境假红）
        subject = str(_gettext("Backup failure alert: {}")).format(PAYLOAD["event"])
        message = MessageContent.objects.filter(title=subject).first()
        assert message is not None
        assert message.level == MessageContent.LevelChoices.DANGER
        assert superuser in message.notice_user.all()
        assert "pg_dump failed" in message.message

        # 同源 60s 内再报：节流为 published=false（不产生第二封）
        second = api_client.post(ALERT_URL, PAYLOAD, format="json", HTTP_X_BACKUP_TOKEN=TOKEN)
        assert second.data["data"]["published"] is False
        assert MessageContent.objects.count() == 1

    def test_different_source_not_throttled(self, api_client, superuser, alert_enabled):
        """不同来源（source）独立计数：媒体包失败与数据库失败各自告警。"""
        api_client.post(ALERT_URL, PAYLOAD, format="json", HTTP_X_BACKUP_TOKEN=TOKEN)
        other = dict(PAYLOAD, source="db-backup-media", event="media archive failed")
        assert (
            api_client.post(ALERT_URL, other, format="json", HTTP_X_BACKUP_TOKEN=TOKEN).data["data"]["published"]
            is True
        )
        assert MessageContent.objects.count() == 2


class TestBackupScriptWiring:
    def test_script_syntax_and_alert_calls(self):
        """备份脚本语法有效，且失败点已接线 send_alert（无需 docker 即可回归）。"""
        script = SERVER_ROOT / "utils" / "db_backup.sh"
        subprocess.run(["bash", "-n", str(script)], check=True)
        text = script.read_text(encoding="utf-8")
        assert "send_alert()" in text
        for event in ("pg_dump failed", "remote sync failed", "media archive failed"):
            assert event in text, event
        # 令牌经请求头传递（不进 URL / 不落日志）
        assert "X-Backup-Token: ${BACKUP_ALERT_TOKEN}" in text
