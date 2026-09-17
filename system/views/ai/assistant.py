#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 助手视图：配置（Setting 体系）+ 状态 + 问答。

- 配置视图与邮件/LDAP 同构：POST create = 连接测试（真实 ping LLM）；
- ask/status 经菜单权限点门控（未授权 403）；问答链路不触生产数据。
"""

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet

from common.core.response import ApiResponse
from common.sdk.ai.chat import AiSdkError, ChatCompletionsClient
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from message.models import ChatRoom
from settings.serializers.ai import AiAssistantSettingSerializer
from settings.views.settings import BaseSettingViewSet
from system.models.ai import AiKnowledgeChunk
from system.models.dataset import Dataset
from system.utils.ai import ai_credentials, ask, is_configured, is_enabled

logger = get_logger(__name__)


class AiAssistantSettingViewSet(BaseSettingViewSet):
    """AI 助手配置与连接测试"""

    serializer_class = AiAssistantSettingSerializer
    category = "ai"

    def create(self, request, *args, **kwargs):
        """测试{cls}：按表单当前值实际 ping 一次 LLM。"""
        serializer = self.get_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        keys = ("AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "AI_TIMEOUT")
        saved = {key: getattr(settings, key) for key in keys}
        try:
            for key in ("AI_BASE_URL", "AI_MODEL", "AI_TIMEOUT"):
                if key in request.data:
                    setattr(settings, key, data.get(key))
            api_key = data.get("AI_API_KEY") or settings.AI_API_KEY
            if not settings.AI_BASE_URL:
                return ApiResponse(code=1001, detail=_("Base URL is required"))
            if not (api_key and settings.AI_MODEL):
                return ApiResponse(code=1001, detail=_("API Key and model are required"))
            original_key = settings.AI_API_KEY
            settings.AI_API_KEY = api_key
            try:
                client = ChatCompletionsClient(ai_credentials())
                reply = client.chat([{"role": "user", "content": "ping"}])
            finally:
                settings.AI_API_KEY = original_key
        except AiSdkError as exc:
            return ApiResponse(code=1002, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 测试入口兜底
            logger.warning("AI connection test unexpected error", exc_info=True)
            return ApiResponse(code=1002, detail=str(exc))
        finally:
            for key, value in saved.items():
                setattr(settings, key, value)
        return ApiResponse(detail=_("AI provider OK: {}").format(reply[:80]))


class AiAssistantViewSet(GenericViewSet):
    """AI 使用/二开助手（基于 docs/ 知识库的 RAG 问答，不触生产数据）"""

    queryset = AiKnowledgeChunk.objects.none()

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="status")
    def status(self, request, *args, **kwargs):
        """助手状态：开关/配置/知识库规模/动作可用性（前端渲染未配置引导与 /do 提示）。"""
        from system.utils.ai_actions import ai_action_enabled, available_actions

        last_synced = AiKnowledgeChunk.objects.order_by("-synced_at").values_list("synced_at", flat=True).first()
        action_ready = ai_action_enabled() and is_enabled()
        return ApiResponse(
            data={
                "enabled": is_enabled(),
                "configured": is_configured(),
                "chunks": AiKnowledgeChunk.objects.count(),
                "synced_at": last_synced.isoformat() if last_synced else "",
                "action_enabled": action_ready,
                "actions": [{"key": spec.key, "label": str(spec.label)} for spec in available_actions(request.user)]
                if action_ready
                else [],
            }
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="metrics")
    def metrics(self, request, *args, **kwargs):
        """AI 调用观测：近 N 天用量 / 成功率 / 日趋势 / 类型分布 / Top 用户。

        数据源 = OperationLog(auth_type=ai)：AI:ask（文档问答）/ AI:nl_query（NL 查数）/
        AI:action（受限动作）；权限点与 status 共用同一路径正则（`(status|metrics)$`，
        见菜单种子），不新增权限点。
        """
        from datetime import timedelta

        from django.db.models import Count
        from django.db.models.functions import TruncDate
        from django.utils import timezone

        from system.models import OperationLog, UserInfo

        try:
            days = int(request.query_params.get("days") or 30)
        except (TypeError, ValueError):
            days = 30
        days = max(1, min(days, 90))
        since = (timezone.now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

        base = OperationLog.objects.filter(
            auth_type=OperationLog.AuthType.AI,
            created_time__gte=since,
        )
        total = base.count()
        failed = base.exclude(status_code=1000).count()

        module_labels = {"AI:ask": "文档问答", "AI:nl_query": "NL 查数", "AI:action": "受限动作"}
        by_module = [
            {
                "module": row["module"] or "",
                "label": module_labels.get(row["module"] or "", row["module"] or "未知"),
                "count": row["count"],
            }
            for row in base.values("module").annotate(count=Count("id")).order_by("-count")
        ]
        by_day = [
            {"date": row["day"].isoformat(), "module": row["module"] or "", "count": row["count"]}
            for row in base.annotate(day=TruncDate("created_time"))
            .values("day", "module")
            .annotate(count=Count("id"))
            .order_by("day")
        ]
        top_rows = (
            base.exclude(object_pk__isnull=True)
            .exclude(object_pk="")
            .values("object_pk")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )
        name_map = {
            str(pk): username
            for pk, username in UserInfo.objects.filter(pk__in=[row["object_pk"] for row in top_rows]).values_list(
                "pk", "username"
            )
        }
        # token 用量（成本维度）：解析窗口内审计 changes 的 usage 字段（缺 usage 的记录记 0）
        import json as _json

        prompt_tokens = completion_tokens = 0
        for raw_changes in base.values_list("changes", flat=True):
            try:
                usage = (_json.loads(raw_changes or "{}") or {}).get("usage") or {}
            except (TypeError, ValueError):
                continue
            try:
                prompt_tokens += int(usage.get("prompt_tokens") or 0)
                completion_tokens += int(usage.get("completion_tokens") or 0)
            except (TypeError, ValueError):
                continue
        return ApiResponse(
            data={
                "days": days,
                "total": total,
                "success": total - failed,
                "failed": failed,
                "by_module": by_module,
                "by_day": by_day,
                "top_users": [
                    {"username": name_map.get(row["object_pk"], row["object_pk"][:12]), "count": row["count"]}
                    for row in top_rows
                ],
                "tokens": {
                    "prompt": prompt_tokens,
                    "completion": completion_tokens,
                    "total": prompt_tokens + completion_tokens,
                },
            }
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="nl-query/interpret")
    def nl_interpret(self, request, *args, **kwargs):
        """NL → 数据集 DSL（白名单校验）+ 试算预览计数（数据权限随调用者）。"""
        from common.sdk.ai.chat import AiSdkError
        from system.utils.ai import is_enabled as ai_enabled_check
        from system.utils.nl_query import (
            audit_nl_query,
            build_interpret_prompt,
            parse_llm_json,
            validate_dsl,
            visible_datasets,
        )

        question = str(request.data.get("question") or "").strip()
        if not question:
            return ApiResponse(code=1001, detail=_("Question cannot be empty"))
        if not settings.AI_NL_QUERY_ENABLED:
            return ApiResponse(code=1001, detail=_("NL query is not enabled"))
        if not ai_enabled_check():
            return ApiResponse(code=1001, detail=_("AI assistant is not enabled or configured"))

        datasets = visible_datasets(request.user)
        if not datasets:
            return ApiResponse(code=1001, detail=_("No visible datasets for NL query"))

        dsl: dict = {}
        normalized: dict = {}
        usage = None
        try:
            client = ChatCompletionsClient(ai_credentials())
            raw = client.chat(build_interpret_prompt(question, datasets))
            usage = getattr(client, "last_usage", None)
            dsl = parse_llm_json(raw)
            normalized = validate_dsl(dsl, request.user)
            dataset = Dataset.objects.get(pk=normalized["dataset"])
            extra = [{"field": f["field"], "op": f["op"], "value": f["value"]} for f in normalized["filters"]]
            from system.utils.dataset import build_queryset

            queryset, model, __ = build_queryset(dataset, request.user, extra_filters=extra)
            preview_count = queryset.count()
        except DjangoValidationError as exc:
            audit_nl_query(request.user, "interpret", question, dsl, error="; ".join(exc.messages), usage=usage)
            return ApiResponse(code=1001, detail="; ".join(exc.messages))
        except AiSdkError as exc:
            audit_nl_query(request.user, "interpret", question, {}, error=str(exc))
            return ApiResponse(code=1001, detail=str(exc))
        audit_nl_query(request.user, "interpret", question, normalized, rows=preview_count, usage=usage)
        return ApiResponse(
            data={
                "dsl": normalized,
                "dataset_name": dataset.name,
                "preview_count": preview_count,
                "mode": normalized["mode"],
            }
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="nl-query/run")
    def nl_run(self, request, *args, **kwargs):
        """执行试算确认后的 DSL：服务端重校验（不信任客户端回传）+ 审计。"""
        from system.utils.ai import is_enabled as ai_enabled_check
        from system.utils.dataset import aggregate_dataset
        from system.utils.nl_query import audit_nl_query, validate_dsl

        dsl = request.data.get("dsl")
        if not settings.AI_NL_QUERY_ENABLED:
            return ApiResponse(code=1001, detail=_("NL query is not enabled"))
        if not ai_enabled_check():
            return ApiResponse(code=1001, detail=_("AI assistant is not enabled or configured"))
        try:
            normalized = validate_dsl(dsl if isinstance(dsl, dict) else {}, request.user)
            dataset = Dataset.objects.get(pk=normalized["dataset"])
            if normalized["mode"] == "aggregate":
                result = aggregate_dataset(
                    dataset,
                    request.user,
                    group_by=normalized["group_by"],
                    metric=normalized["metric"],
                    date_trunc=normalized["date_trunc"] or None,
                    value_field=normalized["value_field"] or None,
                )
                rows = len(result["series"])
            else:
                from system.utils.dataset import build_queryset

                extra = [{"field": f["field"], "op": f["op"], "value": f["value"]} for f in normalized["filters"]]
                queryset, model, columns = build_queryset(dataset, request.user, extra_filters=extra)
                result = {"columns": columns, "rows": list(queryset.values(*columns)[: normalized["limit"]])}
                rows = len(result["rows"])
        except DjangoValidationError as exc:
            audit_nl_query(request.user, "run", "", dsl if isinstance(dsl, dict) else {}, error="; ".join(exc.messages))
            return ApiResponse(code=1001, detail="; ".join(exc.messages))
        audit_nl_query(request.user, "run", "", normalized, rows=rows)
        return ApiResponse(data=result)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="ask")
    def ask(self, request, *args, **kwargs):
        """文档问答：回答引用文档出处；未启用/未配置/无命中/LLM 失败均转可读文案。"""
        from system.utils.ai_actions import audit_ai_ask

        question = str(request.data.get("question") or "")
        try:
            result = ask(question)
        except DjangoValidationError as exc:
            detail = "; ".join(exc.messages)
            audit_ai_ask(request.user, question, ok=False, detail=detail)
            return ApiResponse(code=1001, detail=detail)
        usage = result.pop("_usage", None)
        audit_ai_ask(request.user, question, ok=True, usage=usage)
        return ApiResponse(data=result)

    # ------------------------------------------------------------------
    # A2 受限动作：草稿经聊天 /do 生成，此处只负责「用户确认后」的执行
    # ------------------------------------------------------------------

    @staticmethod
    def _push_action_result(room_id, user, result: dict) -> None:
        """把执行结果落成 AI 房间的 system 消息并广播（跨端可见、刷新可追溯）。"""
        if not room_id:
            return
        try:
            from message import chat as chat_service
            from message.models import ChatMessage
            from message.utils import push_room_event

            room = chat_service.accessible_room(room_id, user)
            if room.room_type != ChatRoom.RoomType.AI:
                return
            detail = str(result.get("detail") or "")
            message, __ = chat_service.create_message(
                room,
                None,
                str(_("Action executed: {}").format(detail))[:2000],
                message_type=ChatMessage.MessageType.SYSTEM,
                extra={"mode": "action", "action_result": result.get("data") or {}},
            )
            push_room_event(room, chat_service.message_payload(message, room=room))
        except Exception:  # noqa: BLE001 结果回写失败不影响执行结果本身
            logger.warning("push ai action result failed", exc_info=True)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="action/execute")
    def action_execute(self, request, *args, **kwargs):
        """执行已确认的动作草稿：白名单 + 参数重校验（不信任前端回传）+ 权限双门 + 审批协议 + 审计。

        审批口径：动作声明需要审批（如需审批的动态表单）时复用 412 协议——
        首次确认建 PENDING 单并返回 412；审批通过后前端原样重发（指纹一致）
        由拦截器携带 X-Approval-Id，消费成功才真正执行。
        """
        from system.utils.ai_actions import ai_action_enabled, audit_ai_action, execute_action, get_action

        action_key = str(request.data.get("action") or "").strip()
        params = request.data.get("params")
        params = params if isinstance(params, dict) else {}
        if not ai_action_enabled():
            return ApiResponse(code=1001, detail=_("AI actions are not enabled"))
        if not is_enabled():
            return ApiResponse(code=1001, detail=_("AI assistant is not enabled or configured"))

        spec = get_action(action_key)
        if spec is None:
            audit_ai_action(request.user, action_key, params, False, str(_("Unknown action")))
            return ApiResponse(code=1001, detail=_("Unknown action"))
        if not spec.available(request.user):
            detail = str(_("The action is not available: {}").format(str(spec.label)))
            audit_ai_action(request.user, action_key, params, False, detail)
            return ApiResponse(code=1001, detail=detail)
        if not spec.has_permission(request.user):
            detail = str(_("You do not have permission to perform the action: {}").format(str(spec.label)))
            audit_ai_action(request.user, action_key, params, False, detail)
            return ApiResponse(code=1001, detail=detail)

        clean, error = spec.validate(request.user, params)
        if error:
            audit_ai_action(request.user, action_key, params, False, error)
            return ApiResponse(code=1001, detail=error)

        if spec.requires_approval(request.user, clean):
            from system.utils.approval import (
                APPROVAL_HEADER,
                APPROVAL_QUERY_PARAM,
                consume_approval,
                create_approval,
                find_active_pending,
                get_request_params,
                pending_response,
            )

            token = request.headers.get(APPROVAL_HEADER) or request.query_params.get(APPROVAL_QUERY_PARAM)
            if token:
                approval_response = consume_approval(request, token)
            else:
                approval = find_active_pending(request.user, request.method, request.path, get_request_params(request))
                if approval is None:
                    # module 显式命名：审批中心里可直接识别来源（视图 docstring 与动作无关）
                    approval = create_approval(None, request, module=str(_("AI action"))[:64])
                approval_response = pending_response(approval)
            if approval_response is not None:
                audit_ai_action(request.user, action_key, clean, False, str(_("Waiting for approval")))
                return approval_response

        result = execute_action(request.user, action_key, clean)
        audit_ai_action(
            request.user,
            action_key,
            clean,
            bool(result.get("ok")),
            str(result.get("detail") or ""),
            {"result": result.get("data") or {}},
        )
        if not result.get("ok"):
            return ApiResponse(code=1001, detail=result.get("detail") or _("Action failed"))
        self._push_action_result(request.data.get("room_id"), request.user, result)
        return ApiResponse(data=result.get("data"), detail=result.get("detail"))
