# -*- coding: utf-8 -*-
"""schema 演进后存储数据的历史键裁剪。

表单字段删除/改名后，旧提交/草稿的 data 含已删字段键——渲染端不展示、用户无法
清理，直接按当前 schema 严格校验必被「Unknown submission keys」拒绝，编辑重提 /
草稿提交 / 驳回重提全部卡死。修复口径（存储回填路径裁剪 + 客户端显式提交仍严格拒绝）：

- trim_stale_schema_keys：纯函数行为（裁剪 + 警告日志 / 非 dict 透传 / 无 fields 透传）；
- PATCH 合并底数：instance.data 先裁剪再与新提交合并（编辑重提不卡死）；
- 草稿 submit：缺省 data 沿用草稿已存数据前先裁剪；
- 客户端显式提交未知键：仍被 validate_submission_data 拒绝（写入侧口径不变）。
"""

import pytest

from dataset.models.dform import DynamicForm, DynamicFormSubmission
from dataset.serializers.dform import DynamicFormSubmissionSerializer
from dataset.utils.dform import trim_stale_schema_keys, validate_submission_data

pytestmark = pytest.mark.django_db

SCHEMA_V1 = {"fields": [{"key": "name", "label": "姓名", "type": "input", "required": True}]}


def _make_form_and_submission(data):
    form = DynamicForm.objects.create(name="裁剪表单", schema=SCHEMA_V1)
    submission = DynamicFormSubmission.objects.create(form=form, data=data)
    return form, submission


class TestTrimStaleSchemaKeys:
    def test_trims_unknown_keys_and_keeps_known(self):
        data = {"name": "张三", "ghost": "x", "renamed_away": 1}
        assert trim_stale_schema_keys(SCHEMA_V1, data) == {"name": "张三"}

    def test_pure_data_passthrough_for_invalid_shapes(self):
        assert trim_stale_schema_keys(SCHEMA_V1, "not-a-dict") == "not-a-dict"
        assert trim_stale_schema_keys(SCHEMA_V1, None) is None
        assert trim_stale_schema_keys({}, {"name": "x"}) == {"name": "x"}
        assert trim_stale_schema_keys({"fields": "bogus"}, {"name": "x"}) == {"name": "x"}

    def test_no_stale_keys_returns_original(self):
        data = {"name": "张三"}
        assert trim_stale_schema_keys(SCHEMA_V1, data) is data


class TestPatchMergeTrimsStoredBase:
    def test_patch_merge_base_trimmed(self):
        """编辑重提（PATCH 局部更新）：合并底数先裁剪历史键，未知键不再卡死。"""
        form, submission = _make_form_and_submission({"name": "张三", "ghost": "x"})
        form.schema = SCHEMA_V1  # schema 未变，仅模拟存量数据含历史键
        serializer = DynamicFormSubmissionSerializer(instance=submission, data={"data": {"name": "李四"}}, partial=True)
        assert serializer.is_valid(), serializer.errors
        instance = serializer.save()
        assert instance.data == {"name": "李四"}

    def test_explicit_unknown_key_still_rejected(self):
        """客户端显式提交未知键仍被拒绝（写入侧严格口径不变，只放行存储回填）。"""
        form, submission = _make_form_and_submission({"name": "张三"})
        serializer = DynamicFormSubmissionSerializer(
            instance=submission, data={"data": {"name": "李四", "ghost": 1}}, partial=True
        )
        assert not serializer.is_valid()
        assert "ghost" in str(serializer.errors)


class TestDraftSubmitTrimsStoredData:
    def test_submit_draft_with_stale_stored_keys(self, superuser):
        """草稿提交：data 缺省沿用草稿已存数据，先裁剪再校验。"""
        form, submission = _make_form_and_submission({"name": "张三", "ghost": "x"})
        submission.status = DynamicFormSubmission.Status.DRAFT
        submission.creator = superuser
        submission.save(update_fields=["status", "creator"])

        from rest_framework.test import APIRequestFactory, force_authenticate

        from dataset.views.dform_submission import DynamicFormSubmissionViewSet

        factory = APIRequestFactory()
        request = factory.post(f"/api/dataset/dynamic-form-submissions/{submission.pk}/submit", {}, format="json")
        force_authenticate(request, user=superuser)
        response = DynamicFormSubmissionViewSet.as_view({"post": "submit"})(request, pk=submission.pk)
        assert response.data.get("code") == 1000, response.data
        submission.refresh_from_db()
        assert "ghost" not in (submission.data or {})
        assert submission.data["name"] == "张三"


class TestValidateSubmissionDataUnchanged:
    def test_unknown_key_rejected_on_explicit_submission(self):
        """validate_submission_data 本身口径不变：未知键显式提交仍拒绝。"""
        with pytest.raises(Exception, match="ghost"):
            validate_submission_data(SCHEMA_V1, {"name": "张三", "ghost": "x"})
