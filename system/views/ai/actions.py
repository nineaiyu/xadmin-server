#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 助手受限动作 action（ViewSet mixin：与 assistant.py 的 AiAssistantViewSet 组合）。

两条链路：
- 草稿生成：聊天室 `/do`（message/ai.py，非流式）与助手页 `action/interpret/stream`
  （本文件，SSE 思考 + 结构化草稿），共用 system/utils/ai_actions.py 的注册表与校验；
- 执行：`action/execute`（用户确认后），白名单 + 参数重校验 + 权限双门 + 审批协议 + 审计。

对话持久化：助手页来源（未携带 room_id）的草稿与执行结果落 AiChatMessage；
聊天室来源由 ChatMessage 承载（room 内消息 + 结果回写），不重复落库。
拆文件仅因行数门禁（500 行）：URL 路径 / 权限点 / 行为与拆分前完全一致。
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from common.core.response import ApiResponse
from common.drf.renders import sse_response
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from message.models import ChatRoom

logger = get_logger(__name__)


class AiActionExecuteMixin:
    """受限动作：草稿生成（流式）+ 执行（白名单 + 参数重校验 + 权限双门 + 审批协议 + 审计）。"""

    @staticmethod
    def _persist_assistant(user, content: str, extra: dict, reasoning: str = "") -> dict:
        """落助手页 assistant 消息（返回载荷；持久化失败返回空 dict）。"""
        from system.utils.ai_chat import message_payload, persist_message

        row = persist_message(user, "action", "assistant", content=content, reasoning=reasoning, extra=extra)
        return message_payload(row)

    @staticmethod
    def _persist_system(user, detail: str) -> dict:
        from system.utils.ai_chat import system_error_message

        return system_error_message(user, "action", detail)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="action/interpret/stream")
    def action_interpret_stream(self, request, *args, **kwargs):
        """指令执行草稿（SSE）：事件序 meta → reasoning* → delta* → done | error。

        LLM 只产出草稿（白名单动作 + 服务端校验后的规范化参数），**不执行**；
        done 载荷 ``{kind: "draft"|"message", draft?, message}``——draft 渲染确认卡片，
        用户确认后走 `action/execute`。门禁（灰度关闭/未配置/空请求）在响应头前
        返回 JSON 1001，与助手页其他流式端点同口径。
        """
        from common.sdk.ai.chat import AiSdkError
        from system.utils.ai import is_enabled as ai_enabled_check
        from system.utils.ai import native_tools_enabled, structured_chat_client
        from system.utils.ai_actions import (
            ai_action_enabled,
            audit_ai_action,
            build_draft_prompt,
            draft_summary,
            parse_draft,
        )
        from system.utils.ai_chat import message_payload, persist_message
        from system.utils.ai_guard import guard_summary
        from system.utils.ai_usage import quota_error, tracked_chat_stream

        text = str(request.data.get("message") or "").strip()
        guard = guard_summary(prompt=text)
        if not ai_action_enabled():
            return ApiResponse(code=1001, detail=_("AI actions are not enabled"), content_type="application/json")
        if not ai_enabled_check():
            return ApiResponse(
                code=1001,
                detail=_("AI assistant is not enabled or configured"),
                content_type="application/json",
            )
        if not text:
            return ApiResponse(code=1001, detail=_("The request cannot be empty"), content_type="application/json")

        user_row = persist_message(request.user, "action", "user", content=text)
        quota_hit = quota_error(request.user, "action")

        def events():
            yield {"event": "meta", "data": {"message": text, "user_message": message_payload(user_row)}}
            chunks: list = []
            reasoning_chunks: list = []
            try:
                if quota_hit:
                    raise DjangoValidationError(quota_hit)
                if native_tools_enabled():
                    # 原生轨道：能力探测通过 + 开关开启时优先（工具调用非散文，一次性调用）
                    from system.utils.ai_actions import native_draft_result

                    result, track = native_draft_result(request.user, text)
                    logger.info("ai action draft track: %s (user=%s)", track, request.user.pk)
                    yield {
                        "event": "delta",
                        "data": {"delta": draft_summary(result.get("drafts") or [result["draft"]])},
                    }
                else:
                    # 双轨对照日志：便于按档案/模型统计两条轨道的成功率
                    logger.info("ai action draft track: prompt (user=%s)", request.user.pk)
                    client, max_tokens = structured_chat_client()
                    for item in tracked_chat_stream(
                        request.user,
                        "action",
                        client,
                        build_draft_prompt(request.user, text),
                        track="prompt",  # 双轨对照：用量账本按轨道统计成功率
                        max_tokens=max_tokens,
                    ):
                        chunk = item.get("text") or ""
                        if not chunk:
                            continue
                        if item.get("type") == "reasoning":
                            reasoning_chunks.append(chunk)
                            yield {"event": "reasoning", "data": {"delta": chunk}}
                        else:
                            chunks.append(chunk)
                            yield {"event": "delta", "data": {"delta": chunk}}
                    raw = "".join(chunks)
                    if not raw.strip():
                        raise DjangoValidationError(
                            _("The model did not provide a final answer; please retry or switch models")
                        )
                    result = parse_draft(raw, request.user)
            except DjangoValidationError as exc:
                detail = "; ".join(getattr(exc, "messages", None) or [str(exc)])
                audit_ai_action(request.user, "", {}, False, detail, {"guard": guard})
                reasoning = "".join(reasoning_chunks)
                if chunks or reasoning:
                    payload = self._persist_assistant(request.user, detail, {"partial": detail}, reasoning)
                else:
                    payload = self._persist_system(request.user, detail)
                yield {"event": "error", "data": {"detail": detail, "message": payload}}
                return
            except AiSdkError as exc:
                from system.utils.ai import readable_ai_error

                detail = readable_ai_error(exc)
                audit_ai_action(request.user, "", {}, False, detail, {"guard": guard})
                reasoning = "".join(reasoning_chunks)
                if chunks or reasoning:
                    payload = self._persist_assistant(request.user, detail, {"partial": detail}, reasoning)
                else:
                    payload = self._persist_system(request.user, detail)
                yield {"event": "error", "data": {"detail": detail, "message": payload}}
                return

            reasoning = "".join(reasoning_chunks)
            if result["kind"] == "message":
                # 模型澄清/不可执行：按普通 AI 气泡渲染（不落草稿）
                row = persist_message(
                    request.user, "action", "assistant", content=result["message"], reasoning=reasoning
                )
                yield {
                    "event": "done",
                    "data": {"kind": "message", "message": message_payload(row)},
                }
                return
            drafts = result.get("drafts") or [result["draft"]]
            # 多动作串联：action_drafts/drafts 供前端逐项渲染确认卡片；
            # action_draft/draft 恒为首个草稿（兼容旧渲染/旧前端）
            extra = {"action_drafts": drafts, "action_draft": drafts[0]}
            done_data = {"kind": "draft", "draft": drafts[0], "drafts": drafts}
            content = drafts[0]["summary"] if len(drafts) == 1 else " → ".join(d["summary"] for d in drafts)
            row = persist_message(
                request.user,
                "action",
                "assistant",
                content=content,
                reasoning=reasoning,
                extra=extra,
            )
            done_data["message"] = message_payload(row)
            yield {"event": "done", "data": done_data}

        return sse_response(events())

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
        from system.utils.ai import is_enabled
        from system.utils.ai_actions import ai_action_enabled, audit_ai_action, execute_action, get_action
        from system.utils.ai_idempotency import execute_idempotent
        from system.utils.ai_usage import quota_error

        assistant_console = not request.data.get("room_id")

        def persist_failure(detail: str) -> None:
            """助手页来源的失败记录落对话流（聊天室来源由 ChatMessage 承载）。"""
            if assistant_console:
                self._persist_system(request.user, detail)

        action_key = str(request.data.get("action") or "").strip()
        params = request.data.get("params")
        params = params if isinstance(params, dict) else {}
        if not ai_action_enabled():
            return ApiResponse(code=1001, detail=_("AI actions are not enabled"))
        if not is_enabled():
            return ApiResponse(code=1001, detail=_("AI assistant is not enabled or configured"))
        # 写类动作配额 fail-closed：审批前置直接拒绝，不产生半执行
        quota = quota_error(request.user, "action")
        if quota:
            audit_ai_action(request.user, action_key, params, False, quota, {"quota": True})
            persist_failure(quota)
            return ApiResponse(code=1001, detail=quota)

        spec = get_action(action_key)
        if spec is None:
            audit_ai_action(request.user, action_key, params, False, str(_("Unknown action")))
            return ApiResponse(code=1001, detail=_("Unknown action"))
        if not spec.available(request.user):
            detail = str(_("The action is not available: {}").format(str(spec.label)))
            audit_ai_action(request.user, action_key, params, False, detail)
            persist_failure(detail)
            return ApiResponse(code=1001, detail=detail)
        if not spec.has_permission(request.user):
            detail = str(_("You do not have permission to perform the action: {}").format(str(spec.label)))
            audit_ai_action(request.user, action_key, params, False, detail)
            persist_failure(detail)
            return ApiResponse(code=1001, detail=detail)

        clean, error = spec.validate(request.user, params)
        if error:
            audit_ai_action(request.user, action_key, params, False, error)
            persist_failure(error)
            return ApiResponse(code=1001, detail=error)

        if spec.requires_approval(request.user, clean):
            from approval.utils.approval import (
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
                persist_failure(str(_("Waiting for approval")))
                return approval_response

        from system.utils.ai_guard import guard_summary

        # 幂等：同一意图（用户 + 动作 + 规范化参数）在 TTL 内重复提交返回首次结果，
        # force=true 为用户确认后的「仍要执行」显式通道
        result = execute_idempotent(
            request.user, action_key, clean, execute_action, force=bool(request.data.get("force"))
        )
        audit_ai_action(
            request.user,
            action_key,
            clean,
            bool(result.get("ok")),
            str(result.get("detail") or ""),
            {
                "result": result.get("data") or {},
                "guard": guard_summary(prompt=action_key),
                "draft_id": result.get("draft_id"),
                "deduplicated": bool(result.get("deduplicated")),
            },
        )
        if not result.get("ok"):
            detail = str(result.get("detail") or _("Action failed"))
            persist_failure(detail)
            return ApiResponse(code=1001, detail=detail)
        if not result.get("deduplicated"):
            self._push_action_result(request.data.get("room_id"), request.user, result)
        message_row = {}
        if assistant_console:
            message_row = self._persist_assistant(
                request.user,
                str(result.get("detail") or _("Operation successful")),
                {"action_result": result.get("data") or {}},
            )
        return ApiResponse(
            data={
                **(result.get("data") or {}),
                "message": message_row,
                # 幂等可见性：命中重复时前端提示「相同操作在 10 分钟内已执行」，
                # 用户确认后可携 force=true 显式重发
                "deduplicated": bool(result.get("deduplicated")),
                "draft_id": result.get("draft_id") or "",
            },
            detail=result.get("detail"),
        )
