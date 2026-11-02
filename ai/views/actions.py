#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 助手受限动作 action（ViewSet mixin：与 assistant.py 的 AiAssistantViewSet 组合）。

两条链路：
- 草稿生成：聊天室 `/do`（message/ai.py，非流式）与助手页 `action/interpret/stream`
  （本文件，SSE 思考 + 结构化草稿），共用 ai/services/actions/ai_actions.py 的注册表与校验；
- 执行：`action/execute`（用户确认后），白名单 + 参数重校验 + 权限双门 + 审批协议 + 审计。

对话持久化：助手页来源（未携带 room_id）的草稿与执行结果落 AiChatMessage；
聊天室来源由 ChatMessage 承载（room 内消息 + 结果回写），不重复落库。
拆文件仅因行数门禁（500 行）：URL 路径 / 权限点 / 行为与拆分前完全一致。
"""

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger, parse_bool

logger = get_logger(__name__)


class AiActionExecuteMixin:
    """受限动作：草稿生成（流式）+ 执行（白名单 + 参数重校验 + 权限双门 + 审批协议 + 审计）。"""

    @staticmethod
    def _persist_assistant(user: Any, content: str, extra: dict[str, Any], reasoning: str = "") -> dict[str, Any]:
        """落助手页 assistant 消息（返回载荷；持久化失败返回空 dict）。"""
        from ai.services.assistant.ai_chat import message_payload, persist_message

        row = persist_message(user, "action", "assistant", content=content, reasoning=reasoning, extra=extra)
        return message_payload(row)

    @staticmethod
    def _persist_system(user: Any, detail: str) -> dict[str, Any]:
        from ai.services.assistant.ai_chat import system_error_message

        return system_error_message(user, "action", detail)

    @staticmethod
    def _force_flag(data: Any) -> bool:
        """执行请求的 force 通道：兼容表单字符串，非法值给 400 而非静默按 False。"""
        try:
            return parse_bool(data.get("force", False))
        except ValueError:
            raise DjangoValidationError(_("Invalid boolean value")) from None

    @staticmethod
    async def _track_frames_async(
        source: Any, chunks: list[Any], reasoning_chunks: list[Any], holder: dict[str, Any]
    ) -> Any:
        """把服务层异步事件流转换为 SSE 帧，结果写入 ``holder["result"]``。

        原生轨道通常没有正文增量：done 前补一条草稿摘要，避免界面空等。
        """
        from ai.services.actions.ai_actions import draft_summary

        async for item in source:
            kind = item.get("type")
            if kind == "reasoning":
                reasoning_chunks.append(item["text"])
                yield {"event": "reasoning", "data": {"delta": item["text"]}}
            elif kind == "content":
                chunks.append(item["text"])
                yield {"event": "delta", "data": {"delta": item["text"]}}
            elif kind == "result":
                holder["result"] = item.get("result") or {}
        result = holder.get("result") or {}
        if result.get("kind") == "draft" and not chunks:
            yield {
                "event": "delta",
                "data": {"delta": draft_summary(result.get("drafts") or [result.get("draft")])},
            }

    @staticmethod
    def _stream_error_frame(
        user: Any, detail: str, guard: Any, chunks: list[Any], reasoning_chunks: list[Any]
    ) -> dict[str, Any]:
        """流内失败统一收口：审计 + 部分产出落助手页，返回 error 帧。"""
        from ai.services.actions.ai_actions import audit_ai_action

        audit_ai_action(user, "", {}, False, detail, {"guard": guard})
        reasoning = "".join(reasoning_chunks)
        if chunks or reasoning:
            payload = AiActionExecuteMixin._persist_assistant(user, detail, {"partial": detail}, reasoning)
        else:
            payload = AiActionExecuteMixin._persist_system(user, detail)
        return {"event": "error", "data": {"detail": detail, "message": payload}}

    @staticmethod
    def _stream_done_frame(user: Any, result: dict[str, Any], reasoning: str) -> dict[str, Any]:
        """成功收口：message 澄清按普通 AI 气泡，draft 渲染确认卡片（多动作兼容旧字段）。"""
        from ai.services.assistant.ai_chat import message_payload, persist_message

        if result["kind"] == "message":
            # 模型澄清/不可执行：按普通 AI 气泡渲染（不落草稿）
            row = persist_message(user, "action", "assistant", content=result["message"], reasoning=reasoning)
            return {"event": "done", "data": {"kind": "message", "message": message_payload(row)}}
        drafts = result.get("drafts") or [result["draft"]]
        # 多动作串联：action_drafts/drafts 供前端逐项渲染确认卡片；
        # action_draft/draft 恒为首个草稿（兼容旧渲染/旧前端）
        row = persist_message(
            user,
            "action",
            "assistant",
            content=drafts[0]["summary"] if len(drafts) == 1 else " → ".join(d["summary"] for d in drafts),
            reasoning=reasoning,
            extra={"action_drafts": drafts, "action_draft": drafts[0]},
        )
        return {
            "event": "done",
            "data": {
                "kind": "draft",
                "draft": drafts[0],
                "drafts": drafts,
                "message": message_payload(row),
            },
        }

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="action/interpret/stream")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def action_interpret_stream(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """指令执行草稿

        SSE：事件序 meta → reasoning* → delta* → done | error

        LLM 只产出草稿（白名单动作 + 服务端校验后的规范化参数），**不执行**；
        done 载荷 ``{kind: "draft"|"message", draft?, message}``——draft 渲染确认卡片，
        用户确认后走 `action/execute`。门禁（灰度关闭/未配置/空请求）在响应头前
        返回 JSON 1001，与助手页其他流式端点同口径。
        """
        from ai.services.actions.ai_actions import ai_action_enabled
        from ai.services.assistant.ai import is_enabled as ai_enabled_check
        from ai.services.assistant.ai import native_tools_enabled
        from ai.services.assistant.ai_chat import message_payload, persist_message
        from ai.services.assistant.ai_guard import guard_summary
        from ai.services.assistant.ai_usage import quota_error
        from common.drf.renders import sse_response_async
        from integrations.sdk.ai.chat import AiSdkError

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
        use_native = native_tools_enabled()
        # meta 载荷在同步段预构建（message_payload 可能触发 FK 懒加载，异步段禁止裸 DB 访问）
        user_payload = message_payload(user_row)

        async def events() -> Any:
            from asgiref.sync import sync_to_async

            from ai.services.actions.ai_draft_tools import (
                native_draft_result_stream_async,
                prompt_draft_result_stream_async,
            )

            yield {"event": "meta", "data": {"message": text, "user_message": user_payload}}
            chunks: list[Any] = []
            reasoning_chunks: list[Any] = []
            holder: dict[str, Any] = {}
            try:
                if quota_hit:
                    raise DjangoValidationError(quota_hit)
                if use_native:
                    logger.info("ai action draft track: native (user=%s)", request.user.pk)
                    source = native_draft_result_stream_async(request.user, text)
                else:
                    # 双轨对照日志：便于按档案/模型统计两条轨道的成功率
                    logger.info("ai action draft track: prompt (user=%s)", request.user.pk)
                    source = prompt_draft_result_stream_async(request.user, text)
                async for frame in self._track_frames_async(source, chunks, reasoning_chunks, holder):
                    yield frame
                result = holder.get("result") or {}
                if not result:
                    raise DjangoValidationError(_("The model did not return an actionable request"))
            except DjangoValidationError as exc:
                detail = "; ".join(getattr(exc, "messages", None) or [str(exc)])
                yield await sync_to_async(self._stream_error_frame)(
                    request.user, detail, guard, chunks, reasoning_chunks
                )
                return
            except AiSdkError as exc:
                from ai.services.assistant.ai import readable_ai_error

                yield await sync_to_async(self._stream_error_frame)(
                    request.user, readable_ai_error(exc), guard, chunks, reasoning_chunks
                )
                return
            except Exception:  # noqa: BLE001 流内未知异常兜底：转 error 帧，避免 ASGI 直接断流
                # 典型场景：模型返回了未预期的结构（客户端只看到连接被截断）。异常细节只进日志。
                logger.exception("ai action draft stream failed")
                yield await sync_to_async(self._stream_error_frame)(
                    request.user, str(_("AI service is temporarily unavailable")), guard, chunks, reasoning_chunks
                )
                return

            yield await sync_to_async(self._stream_done_frame)(request.user, result, "".join(reasoning_chunks))

        return sse_response_async(events())

    @staticmethod
    def _push_action_result(room_id: Any, user: Any, result: dict[str, Any]) -> None:
        """把执行结果落成 AI 房间的 system 消息并广播（跨端可见、刷新可追溯）。"""
        if not room_id:
            return
        try:
            from message import chat as chat_service
            from message.models import ChatMessage
            from message.ws.utils import push_room_event

            room = chat_service.accessible_room(room_id, user)
            from message.models import ChatRoom

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

    @staticmethod
    def _resolve_action(request: Any, action_key: str, params: dict[str, Any], persist_failure: Any) -> Any:
        """动作解析与三重校验（可用性 / 权限 / 参数），失败返回 ApiResponse，成功返回 (spec, clean)。"""
        from ai.services.actions.ai_actions import audit_ai_action, get_action

        # 传 user：mcp.* 动态动作按当前用户现查（无 user 的解析不到动态 key）
        spec = get_action(action_key, request.user)
        if spec is None:
            audit_ai_action(request.user, action_key, params, False, str(_("Unknown action")))
            return ApiResponse(code=1001, detail=_("Unknown action"))
        if not spec.available(request.user):
            detail = str(_("The action is not available: {}").format(str(spec.label)))
        elif not spec.has_permission(request.user):
            detail = str(_("You do not have permission to perform the action: {}").format(str(spec.label)))
        else:
            clean, error = spec.validate(request.user, params)
            if not error:
                return spec, clean
            detail = error
        audit_ai_action(request.user, action_key, params, False, detail)
        persist_failure(detail)
        return ApiResponse(code=1001, detail=detail)

    @staticmethod
    def _approval_gate(request: Any, clean: dict[str, Any]) -> Any:
        """审批协议门：需要审批的动作按 412 一次性令牌协议拦截（放行返回 None）。"""
        from approval.services.approval import (
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
            return consume_approval(request, token)
        approval = find_active_pending(request.user, request.method, request.path, get_request_params(request))
        if approval is None:
            # module 显式命名：审批中心里可直接识别来源（视图 docstring 与动作无关）
            approval = create_approval(None, request, module=str(_("AI action"))[:64])
        return pending_response(approval)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="action/execute")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def action_execute(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """执行已确认的动作草稿

        白名单 + 参数重校验（不信任前端回传）+ 权限双门 + 审批协议 + 审计

        审批口径：动作声明需要审批（如需审批的动态表单）时复用 412 协议——
        首次确认建 PENDING 单并返回 412；审批通过后前端原样重发（指纹一致）
        由拦截器携带 X-Approval-Id，消费成功才真正执行。
        """
        from ai.services.actions.ai_actions import ai_action_enabled, audit_ai_action, execute_action
        from ai.services.assistant.ai import is_enabled
        from ai.services.assistant.ai_idempotency import execute_idempotent
        from ai.services.assistant.ai_usage import quota_error

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

        resolved = self._resolve_action(request, action_key, params, persist_failure)
        if isinstance(resolved, ApiResponse):
            return resolved
        spec, clean = resolved

        if spec.requires_approval(request.user, clean):
            approval_response = self._approval_gate(request, clean)
            if approval_response is not None:
                audit_ai_action(request.user, action_key, clean, False, str(_("Waiting for approval")))
                persist_failure(str(_("Waiting for approval")))
                return approval_response

        # 幂等：同一意图（用户 + 动作 + 规范化参数）在 TTL 内重复提交返回首次结果，
        # force=true 为用户确认后的「仍要执行」显式通道
        from functools import partial

        from ai.services.assistant.ai_guard import guard_summary
        from common.core.oplog_recorder import desensitize_payload

        force = self._force_flag(request.data)
        result = execute_idempotent(
            request.user,
            action_key,
            clean,
            partial(execute_action, request=request),
            force=force,
        )
        audit_ai_action(
            request.user,
            action_key,
            clean,
            bool(result.get("ok")),
            str(result.get("detail") or ""),
            {
                "result": desensitize_payload(result.get("data") or {}),
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
