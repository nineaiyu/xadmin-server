#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : menu
# author : ly_13
# date : 6/6/2023


from django_filters import rest_framework as filters
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.views import APIView

from common.base.utils import menu_list_to_tree, get_choices_dict, format_menu_data
from common.core.modelset import BaseModelSet
from common.core.pagination import MenuPageNumber
from common.core.response import ApiResponse
from common.core.utils import get_all_url_dict
from system.models import Menu
from system.utils.serializer import MenuSerializer, RouteSerializer


class MenuFilter(filters.FilterSet):
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    component = filters.CharFilter(field_name='component', lookup_expr='icontains')
    title = filters.CharFilter(field_name='title', lookup_expr='icontains')
    path = filters.CharFilter(field_name='path', lookup_expr='icontains')

    class Meta:
        model = Menu
        fields = ['name']


class MenuView(BaseModelSet):
    queryset = Menu.objects.order_by('rank').all()
    serializer_class = MenuSerializer
    pagination_class = MenuPageNumber

    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['updated_time', 'name', 'created_time']
    filterset_class = MenuFilter

    # def create(self, request, *args, **kwargs):
    #     serializer = MenuMetaSerializer(data=request.data.get('meta', {}))
    #     serializer.is_valid(raise_exception=True)
    #     meta_obj = serializer.save()

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, choices_dict=get_choices_dict(Menu.method_choices),
                           api_url_list=get_all_url_dict(''))

    @action(methods=['post'], detail=False)
    def action_rank(self, request, *args, **kwargs):
        action = request.data.get('action')
        if action == 'order':
            pks = request.data.get('pks', [])
            rank = 0
            for pk in pks:
                self.queryset.filter(pk=pk).update(rank=rank)
                rank += 1
            return ApiResponse(detail='菜单顺序保存成功')
        return ApiResponse(code=1001, detail="操作失败")


class UserRoutesView(APIView):

    def get(self, request):
        menu_list = []
        user_obj = request.user
        if user_obj.is_superuser:
            menu_list = RouteSerializer(Menu.objects.filter(is_active=True, menu_type__in=[0, 1]).order_by('rank'),
                                        many=True, context={'user': request.user}).data

            return ApiResponse(data=format_menu_data(menu_list_to_tree(menu_list)))
        else:
            if user_obj.roles:
                menu_obj = Menu.objects.filter(userrole__in=user_obj.roles.all()).all().distinct()
            else:
                menu_obj = None

        if menu_obj:
            menu_list = RouteSerializer(menu_obj.filter(is_active=True, menu_type__in=[0, 1]).order_by('rank'),
                                        many=True, context={'user': request.user}).data

        return ApiResponse(data=format_menu_data(menu_list_to_tree(menu_list)))
