# -*- coding: utf-8 -*-
"""AI 知识库文档管理集成测试。

覆盖：上传（同名覆盖/校验）、列表轻量与详情预览（全文 + 分块摘要）、
检索即时命中、删除与启停的分块联动、仓库文档保护、sync 与上传互不越界、
权限门控。检索面即分块表——上传/删除/停用后无需额外动作即生效。
"""

import pytest

from system.models.ai import AiKnowledgeChunk, AiKnowledgeDocument
from system.utils.ai import retrieve

pytestmark = pytest.mark.django_db

KNOWLEDGE_URL = "/api/system/ai/knowledge-documents"

DOC_CONTENT = """# 内网使用指南

## 登录说明

首次登录使用管理员分配的初始密码，登录后立即修改。

## 报表导出

报表导出在下载中心查看进度，支持 xlsx 格式。
"""


def _upload(client, name="内网使用指南", content=DOC_CONTENT):
    return client.post(KNOWLEDGE_URL, {"name": name, "content": content}, format="json")


class TestUpload:
    def test_upload_creates_document_and_chunks(self, auth_client):
        response = _upload(auth_client)
        assert response.status_code == 200, response.data
        doc = AiKnowledgeDocument.objects.get(title="内网使用指南")
        assert doc.source_type == AiKnowledgeDocument.SourceType.UPLOAD
        assert doc.path == "upload/内网使用指南.md"
        assert doc.chunk_count == 3
        assert AiKnowledgeChunk.objects.filter(source_path=doc.path).count() == 3
        assert doc.creator is not None
        # 检索即时命中（无需额外重建动作）
        hits = retrieve("报表导出在哪里看")
        assert any("下载中心" in item["chunk"].content for item in hits)

    def test_same_name_overwrites_in_place(self, auth_client):
        assert _upload(auth_client).status_code == 200
        updated = _upload(auth_client, content="# 内网使用指南\n\n## 新章节\n\n内容已更新")
        assert updated.status_code == 200
        assert AiKnowledgeDocument.objects.filter(title="内网使用指南").count() == 1
        doc = AiKnowledgeDocument.objects.get(title="内网使用指南")
        assert doc.chunk_count == 2
        assert not AiKnowledgeChunk.objects.filter(source_path=doc.path, content__contains="下载中心").exists()

    @pytest.mark.parametrize(
        "payload",
        [
            {"name": "   ", "content": "内容"},
            {"name": "a/b", "content": "内容"},
            {"name": "a\\b", "content": "内容"},
            {"name": "..", "content": "内容"},
            {"name": "指南", "content": "   "},
        ],
    )
    def test_upload_validation(self, auth_client, payload):
        response = auth_client.post(KNOWLEDGE_URL, payload, format="json")
        assert response.status_code == 400

    def test_upload_content_too_large(self, auth_client):
        response = _upload(auth_client, name="超大文档", content="x" * 200_001)
        assert response.status_code == 400


class TestPreviewAndList:
    def test_retrieve_returns_content_and_chunks(self, auth_client):
        doc_pk = _upload(auth_client).json()["data"]["pk"]
        body = auth_client.get(f"{KNOWLEDGE_URL}/{doc_pk}").json()["data"]
        assert "报表导出在下载中心" in body["content"]
        assert len(body["chunks"]) == 3
        assert body["chunks"][0]["index"] == 0 and body["chunks"][0]["size"] > 0

    def test_list_is_lightweight(self, auth_client):
        _upload(auth_client)
        rows = auth_client.get(KNOWLEDGE_URL).json()["data"]["results"]
        assert rows and "content" not in rows[0] and "chunks" not in rows[0]
        assert rows[0]["chunk_count"] == 3


class TestDeleteAndToggle:
    def test_delete_upload_removes_chunks(self, auth_client):
        doc_pk = _upload(auth_client).json()["data"]["pk"]
        assert auth_client.delete(f"{KNOWLEDGE_URL}/{doc_pk}").status_code == 200
        assert not AiKnowledgeChunk.objects.filter(source_path__startswith="upload/").exists()
        assert retrieve("报表导出在哪里看") == []

    def test_repo_document_delete_rejected(self, auth_client):
        repo_doc = AiKnowledgeDocument.objects.create(
            path="docs/manual.md", title="仓库手册", content="内容", source_type=AiKnowledgeDocument.SourceType.REPO
        )
        response = auth_client.delete(f"{KNOWLEDGE_URL}/{repo_doc.pk}")
        assert response.status_code == 400
        assert AiKnowledgeDocument.objects.filter(pk=repo_doc.pk).exists()

    def test_toggle_active_removes_and_restores_chunks(self, auth_client):
        doc_pk = _upload(auth_client).json()["data"]["pk"]
        off = auth_client.patch(f"{KNOWLEDGE_URL}/{doc_pk}", {"is_active": False}, format="json")
        assert off.status_code == 200, off.data
        assert AiKnowledgeChunk.objects.filter(source_path__startswith="upload/").count() == 0
        assert retrieve("报表导出在哪里看") == []
        on = auth_client.patch(f"{KNOWLEDGE_URL}/{doc_pk}", {"is_active": True}, format="json")
        assert on.status_code == 200, on.data
        assert AiKnowledgeChunk.objects.filter(source_path__startswith="upload/").count() == 3


class TestSyncIsolation:
    def test_sync_keeps_upload_documents(self, tmp_path, monkeypatch, auth_client):
        """关键守护：仓库同步（含清理）不得触碰上传文档及其分块。"""
        from system.utils import ai as ai_utils

        _upload(auth_client)
        empty_docs = tmp_path / "docs"
        empty_docs.mkdir()
        monkeypatch.setattr(ai_utils, "DOCS_DIR", empty_docs)
        monkeypatch.setattr(ai_utils, "ROOT_DOCS", [])
        summary = ai_utils.sync_knowledge()
        assert summary["removed"] == 0
        assert AiKnowledgeChunk.objects.filter(source_path__startswith="upload/").count() == 3
        assert any("下载中心" in item["chunk"].content for item in retrieve("报表导出在哪里看"))


class TestRepoRebuildAction:
    def test_sync_repo_action(self, auth_client, tmp_path, monkeypatch):
        from system.utils import ai as ai_utils

        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "guide.md").write_text("# Guide\n\n## Setup\n\n安装说明内容", encoding="utf-8")
        monkeypatch.setattr(ai_utils, "DOCS_DIR", docs)
        monkeypatch.setattr(ai_utils, "ROOT_DOCS", [])
        response = auth_client.post(f"{KNOWLEDGE_URL}/sync-repo", {}, format="json")
        assert response.status_code == 200, response.data
        assert response.json()["data"]["created"] == 1


class TestBatchOperations:
    def test_batch_destroy_only_uploads_and_clears_chunks(self, auth_client):
        """批量删除：只删上传文档（repo 静默保留），分块随删除清理。"""
        _upload(auth_client, name="批量甲")
        _upload(auth_client, name="批量乙")
        repo = AiKnowledgeDocument.objects.create(
            path="docs/manual.md",
            title="仓库手册",
            content="内容",
            source_type=AiKnowledgeDocument.SourceType.REPO,
        )
        pks = [
            str(pk)
            for pk in AiKnowledgeDocument.objects.filter(source_type=AiKnowledgeDocument.SourceType.UPLOAD).values_list(
                "pk", flat=True
            )
        ]
        assert len(pks) == 2
        response = auth_client.post(f"{KNOWLEDGE_URL}/batch-destroy", pks, format="json")
        assert response.status_code == 200, response.data
        assert not AiKnowledgeDocument.objects.filter(source_type=AiKnowledgeDocument.SourceType.UPLOAD).exists()
        # 分块必须随删除清理（框架批量删除不触发 perform_destroy，此处逐条清理）
        assert not AiKnowledgeChunk.objects.filter(source_path__startswith="upload/").exists()
        assert AiKnowledgeDocument.objects.filter(pk=repo.pk).exists()

    def test_batch_toggle_updates_chunks(self, auth_client):
        """批量停用 → 分块移除退出检索；批量启用 → 分块重建。"""
        _upload(auth_client, name="批量启停甲")
        _upload(auth_client, name="批量启停乙")
        pks = [
            str(pk)
            for pk in AiKnowledgeDocument.objects.filter(source_type=AiKnowledgeDocument.SourceType.UPLOAD).values_list(
                "pk", flat=True
            )
        ]
        off = auth_client.post(f"{KNOWLEDGE_URL}/batch-toggle", {"pks": pks, "is_active": False}, format="json")
        assert off.status_code == 200, off.data
        assert off.json()["data"]["changed"] == 2
        assert AiKnowledgeChunk.objects.filter(source_path__startswith="upload/").count() == 0
        on = auth_client.post(f"{KNOWLEDGE_URL}/batch-toggle", {"pks": pks, "is_active": True}, format="json")
        assert on.status_code == 200, on.data
        assert on.json()["data"]["changed"] == 2
        assert AiKnowledgeChunk.objects.filter(source_path__startswith="upload/").count() == 6

    @pytest.mark.parametrize("payload", [{"pks": []}, {"is_active": True}])
    def test_batch_toggle_validation(self, auth_client, payload):
        response = auth_client.post(f"{KNOWLEDGE_URL}/batch-toggle", payload, format="json")
        assert response.status_code == 400


class TestPermissions:
    def test_anonymous_rejected(self, api_client):
        assert api_client.get(KNOWLEDGE_URL).status_code == 401

    def test_normal_user_without_menu_rejected(self, normal_user):
        from rest_framework.test import APIClient

        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        assert client.get(KNOWLEDGE_URL).status_code == 403

    def test_normal_user_with_menu_allowed(self, normal_user):
        from rest_framework.test import APIClient

        from system.models import Menu, MenuMeta

        meta = MenuMeta.objects.create(title="list:AiKnowledge")
        menu = Menu.objects.create(
            name="list:AiKnowledge",
            path="api/system/ai/knowledge-documents$",
            method="GET",
            menu_type=Menu.MenuChoices.PERMISSION,
            meta=meta,
        )
        normal_user.roles.first().menu.set([menu])
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        assert client.get(KNOWLEDGE_URL).status_code == 200
