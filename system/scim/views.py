# -*- coding: utf-8 -*-
"""SCIM 2.0 视图（RFC 7644 子集）。

- 端点：ServiceProviderConfig / Schemas / ResourceTypes / Users / Groups；
- 请求与响应媒体类型兼容 `application/scim+json`（IdP 标准）与 `application/json`；
- 错误统一 RFC 7644 §3.12 Error 文档；未支持的能力（排序/复杂 filter）返回
  `invalidFilter`/`invalidValue` 而非静默忽略；
- 所有写操作落审计（resources.write_audit）。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    PermissionDenied,
    Throttled,
    ValidationError,
)
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from system.models import UserInfo, UserRole
from system.scim.auth import ScimThrottle, ScimTokenAuthentication, ScimTokenPermission
from system.scim.resources import (
    LIST_COUNT_MAX,
    SCHEMA_GROUP,
    SCHEMA_LIST,
    SCHEMA_RESOURCE_TYPE,
    SCHEMA_SERVICE_PROVIDER,
    SCHEMA_USER,
    ScimApiError,
    create_group,
    create_user,
    deactivate_user,
    delete_group,
    error_response,
    group_resource,
    list_response,
    parse_filter,
    parse_paging,
    patch_group,
    patch_user,
    update_group,
    update_user,
    user_resource,
    write_audit,
)

SCIM_MEDIA_TYPE = "application/scim+json"


class ScimJSONParser(JSONParser):
    """兼容 IdP 标准媒体类型 `application/scim+json` 的请求体解析。"""

    media_type = SCIM_MEDIA_TYPE


class ScimView(APIView):
    """SCIM 基类：独立凭证鉴权 + 凭证级限流 + 统一错误文档。"""

    authentication_classes = [ScimTokenAuthentication]
    permission_classes = [ScimTokenPermission]
    throttle_classes = [ScimThrottle]
    parser_classes = [JSONParser, ScimJSONParser]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Content-Type"] = SCIM_MEDIA_TYPE
        return response

    def handle_exception(self, exc):
        if isinstance(exc, ScimApiError):
            return Response(error_response(exc.status, exc.detail, exc.scim_type), status=exc.status)
        if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
            return Response(error_response(401, str(_("Authentication failed"))), status=401)
        if isinstance(exc, PermissionDenied):
            detail = str(getattr(exc, "detail", _("Permission denied")))
            return Response(error_response(403, detail), status=403)
        if isinstance(exc, Throttled):
            return Response(error_response(429, str(_("Request was throttled"))), status=429)
        if isinstance(exc, ValidationError):
            return Response(error_response(400, str(exc.detail), "invalidValue"), status=400)
        if getattr(exc, "status_code", None) == 404:
            return Response(error_response(404, str(_("Resource not found"))), status=404)
        return super().handle_exception(exc)


def _get_user(pk):
    user = UserInfo.all_objects.filter(pk=pk).first() if str(pk).isdigit() else None
    if user is None:
        raise ScimApiError(404, str(_("User not found")))
    return user


def _get_group(pk):
    group = UserRole.objects.filter(pk=pk).first()
    if group is None:
        raise ScimApiError(404, str(_("Group not found")))
    return group


class ServiceProviderConfigView(ScimView):
    """能力声明：IdP 据此决定是否启用 PATCH / 过滤 / 批量等（未实现的能力显式 false）。"""

    def get(self, request):
        return Response(
            {
                "schemas": [SCHEMA_SERVICE_PROVIDER],
                "documentationUri": "/api/scim/v2",
                "patch": {"supported": True},
                "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
                "filter": {"supported": True, "maxResults": LIST_COUNT_MAX},
                "changePassword": {"supported": False},
                "sort": {"supported": False},
                "etag": {"supported": False},
                "authenticationSchemes": [
                    {
                        "type": "oauthbearertoken",
                        "name": "SCIM Token",
                        "description": "Static bearer token configured in system settings (SCIM_TOKEN)",
                        "primary": True,
                    }
                ],
            }
        )


class SchemasView(ScimView):
    """支持的 Schema 列表（User / Group 核心属性子集）。"""

    def get(self, request):
        return Response(
            {
                "schemas": [SCHEMA_LIST],
                "totalResults": 2,
                "startIndex": 1,
                "itemsPerPage": 2,
                "Resources": [
                    {
                        "id": SCHEMA_USER,
                        "name": "User",
                        "description": "User Account (subset: userName/name/displayName/emails/phoneNumbers/active)",
                        "attributes": [
                            {"name": "userName", "type": "string", "required": True, "uniqueness": "server"},
                            {"name": "displayName", "type": "string", "required": False},
                            {"name": "emails", "type": "complex", "multiValued": True, "required": False},
                            {"name": "phoneNumbers", "type": "complex", "multiValued": True, "required": False},
                            {"name": "active", "type": "boolean", "required": False},
                        ],
                        "meta": {"resourceType": "Schema"},
                    },
                    {
                        "id": SCHEMA_GROUP,
                        "name": "Group",
                        "description": "Group (subset: displayName/members)",
                        "attributes": [
                            {"name": "displayName", "type": "string", "required": True},
                            {"name": "members", "type": "complex", "multiValued": True, "required": False},
                        ],
                        "meta": {"resourceType": "Schema"},
                    },
                ],
            }
        )


class ResourceTypesView(ScimView):
    def get(self, request):
        return Response(
            {
                "schemas": [SCHEMA_LIST],
                "totalResults": 2,
                "startIndex": 1,
                "itemsPerPage": 2,
                "Resources": [
                    {
                        "schemas": [SCHEMA_RESOURCE_TYPE],
                        "id": "User",
                        "name": "User",
                        "endpoint": "/Users",
                        "schema": SCHEMA_USER,
                        "meta": {"resourceType": "ResourceType"},
                    },
                    {
                        "schemas": [SCHEMA_RESOURCE_TYPE],
                        "id": "Group",
                        "name": "Group",
                        "endpoint": "/Groups",
                        "schema": SCHEMA_GROUP,
                        "meta": {"resourceType": "ResourceType"},
                    },
                ],
            }
        )


class UsersView(ScimView):
    """GET /Users（filter=userName eq "x" + 分页）/ POST /Users（开通）。"""

    def get(self, request):
        start_index, count = parse_paging(request.query_params)
        attribute, value = parse_filter(request.query_params.get("filter", ""))
        queryset = UserInfo.all_objects.all()
        if attribute == "userName":
            queryset = queryset.filter(username=value)
        elif attribute == "id":
            queryset = queryset.filter(pk=value) if value.isdigit() else queryset.none()
        elif attribute:
            raise ScimApiError(400, str(_("Unsupported filter attribute")), scim_type="invalidFilter")

        total = queryset.count()
        page = queryset.order_by("pk")[start_index - 1 : start_index - 1 + count]
        resources = [user_resource(user) for user in page]
        return Response(list_response(resources, total, start_index))

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        user = create_user(payload)
        write_audit(
            request,
            action="create",
            object_pk=str(user.pk),
            changes={"username": user.username, "active": user.is_active},
        )
        return Response(user_resource(user), status=status.HTTP_201_CREATED)


class UserDetailView(ScimView):
    """GET / PUT / PATCH / DELETE（DELETE = 停用，RFC 7644 §3.6）。"""

    def get(self, request, pk):
        return Response(user_resource(_get_user(pk)))

    def put(self, request, pk):
        user = _get_user(pk)
        payload = request.data if isinstance(request.data, dict) else {}
        was_active = bool(user.is_active)
        changes = update_user(user, payload)
        if was_active and not user.is_active:
            deactivate_user(user)
        write_audit(request, action="replace", object_pk=str(user.pk), changes={key: True for key in changes})
        return Response(user_resource(user))

    def patch(self, request, pk):
        user = _get_user(pk)
        payload = request.data if isinstance(request.data, dict) else {}
        operations = payload.get("Operations")
        if not isinstance(operations, list):
            raise ScimApiError(400, str(_("Operations is required")), scim_type="invalidSyntax")
        was_active = bool(user.is_active)
        changes = patch_user(user, operations)
        if was_active and not user.is_active:
            deactivate_user(user)
        write_audit(request, action="patch", object_pk=str(user.pk), changes={key: True for key in changes})
        return Response(user_resource(user))

    def delete(self, request, pk):
        user = _get_user(pk)
        deactivate_user(user)
        write_audit(request, action="deactivate", object_pk=str(user.pk), changes={"is_active": False})
        return Response(status=status.HTTP_204_NO_CONTENT)


class GroupsView(ScimView):
    def get(self, request):
        start_index, count = parse_paging(request.query_params)
        attribute, value = parse_filter(request.query_params.get("filter", ""))
        queryset = UserRole.objects.all()
        if attribute == "displayName":
            queryset = queryset.filter(name=value)
        elif attribute == "externalId":
            queryset = queryset.filter(code=value)
        elif attribute == "id":
            queryset = queryset.filter(pk=value)
        elif attribute:
            raise ScimApiError(400, str(_("Unsupported filter attribute")), scim_type="invalidFilter")

        total = queryset.count()
        page = queryset.order_by("pk")[start_index - 1 : start_index - 1 + count]
        return Response(list_response([group_resource(role) for role in page], total, start_index))

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        role = create_group(payload)
        write_audit(request, action="create_group", object_pk=str(role.pk), changes={"code": role.code})
        return Response(group_resource(role), status=status.HTTP_201_CREATED)


class GroupDetailView(ScimView):
    def get(self, request, pk):
        return Response(group_resource(_get_group(pk)))

    def put(self, request, pk):
        role = _get_group(pk)
        payload = request.data if isinstance(request.data, dict) else {}
        update_group(role, payload)
        write_audit(request, action="replace_group", object_pk=str(role.pk), changes={"code": role.code})
        return Response(group_resource(role))

    def patch(self, request, pk):
        role = _get_group(pk)
        payload = request.data if isinstance(request.data, dict) else {}
        operations = payload.get("Operations")
        if not isinstance(operations, list):
            raise ScimApiError(400, str(_("Operations is required")), scim_type="invalidSyntax")
        patch_group(role, operations)
        write_audit(request, action="patch_group", object_pk=str(role.pk), changes={"code": role.code})
        return Response(group_resource(role))

    def delete(self, request, pk):
        role = _get_group(pk)
        write_audit(request, action="delete_group", object_pk=str(role.pk), changes={"code": role.code})
        delete_group(role)
        return Response(status=status.HTTP_204_NO_CONTENT)
