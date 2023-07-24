#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : serializer
# author : ly_13
# date : 6/6/2023
from typing import OrderedDict

from django.conf import settings
from rest_framework import serializers

from system import models


class UserInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.UserInfo
        fields = ['username', 'nickname', 'email', 'last_login', 'sex', 'date_joined', 'pk', 'mobile',
                  'is_active', 'roles', 'avatar', 'roles_info']
        extra_kwargs = {'last_login': {'read_only': True}, 'date_joined': {'read_only': True},
                        'pk': {'read_only': True}, 'avatar': {'read_only': True}}
        # extra_kwargs = {'password': {'write_only': True}}
        read_only_fields = ['pk'] + list(set([x.name for x in models.UserInfo._meta.fields]) - set(fields))

    roles_info = serializers.SerializerMethodField(read_only=True)

    def get_roles_info(self, obj):
        result = []
        if isinstance(obj, OrderedDict):
            role_queryset = obj.get('roles')
        else:
            role_queryset = obj.roles.all()
        if role_queryset:
            for role in role_queryset:
                result.append({'pk': role.pk, 'name': role.name})
        return result


class UserInfoUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.UserInfo
        fields = ['first_name', 'old_password', 'new_password']
        extra_kwargs = {
            "old_password": {"write_only": True},
            "new_password": {"write_only": True},
        }

    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True)

    def validate(self, attrs):
        attrs['first_name'] = attrs['first_name'][:8]
        return attrs

    def update(self, instance, validated_data):
        old_password = validated_data.get("old_password")
        new_password = validated_data.get("new_password")
        if old_password and new_password:
            if not instance.check_password(validated_data.get("old_password")):
                raise Exception('旧密码校验失败')
            instance.set_password(validated_data.get("new_password"))
            instance.save()
            return instance
        return super(UserInfoUpdateSerializer, self).update(instance, validated_data)


class RouteMetaSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.MenuMeta
        fields = ['title', 'icon', 'showParent', 'showLink', 'extraIcon', 'keepAlive', 'frameSrc', 'frameLoading',
                  'transition', 'hiddenTag', 'dynamicLevel', 'auths']

    showParent = serializers.BooleanField(source='is_show_parent', read_only=True)
    showLink = serializers.BooleanField(source='is_show_menu', read_only=True)
    extraIcon = serializers.CharField(source='r_svg_name', read_only=True)
    keepAlive = serializers.BooleanField(source='is_keepalive', read_only=True)
    frameSrc = serializers.CharField(source='frame_url', read_only=True)
    frameLoading = serializers.BooleanField(source='frame_loading', read_only=True)

    transition = serializers.SerializerMethodField()

    def get_transition(self, obj):
        return {
            'enterTransition': obj.transition_enter,
            'leaveTransition': obj.transition_leave,
        }

    hiddenTag = serializers.BooleanField(source='is_hidden_tag', read_only=True)
    dynamicLevel = serializers.IntegerField(source='dynamic_level', read_only=True)

    auths = serializers.SerializerMethodField()

    def get_auths(self, obj):
        user = self.context.get('user')
        if user.is_superuser:
            menu_obj = models.Menu.objects
        else:
            menu_obj = models.Menu.objects.filter(userrole__in=user.roles.all()).distinct()
        queryset = menu_obj.filter(menu_type=2, parent=obj.menu, is_active=True).values('name').distinct()
        return [x['name'] for x in queryset]


class MenuMetaSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.MenuMeta
        fields = '__all__'


class MenuSerializer(serializers.ModelSerializer):
    meta = MenuMetaSerializer()

    class Meta:
        model = models.Menu
        fields = ['pk', 'name', 'rank', 'path', 'component', 'meta', 'parent', 'menu_type', 'is_active']
        read_only_fields = ['pk']
        extra_kwargs = {'rank': {'read_only': True}}

    def update(self, instance, validated_data):
        serializer = MenuMetaSerializer(instance.meta, data=validated_data.pop('meta'), partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return super().update(instance, validated_data)

    def create(self, validated_data):
        serializer = MenuMetaSerializer(data=validated_data.pop('meta'))
        serializer.is_valid(raise_exception=True)
        validated_data['meta'] = serializer.save()
        return super().create(validated_data)


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.UserRole
        fields = ['pk', 'name', 'is_active', 'code', 'menu', 'description', 'created_time']
        read_only_fields = ['pk']


class RouteSerializer(MenuSerializer):
    meta = RouteMetaSerializer()


class OperationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.OperationLog
        fields = ["pk", "owner", "module", "path", "body", "method", "ipaddress", "browser", "system", "response_code",
                  "response_result", "status_code", "created_time"]
        read_only_fields = ["pk"] + list(set([x.name for x in models.OperationLog._meta.fields]))

    owner = serializers.SerializerMethodField()
    module = serializers.SerializerMethodField()

    def get_owner(self, obj):
        if obj.owner:
            return {'pk': obj.owner.pk, 'username': obj.owner.username}
        return {}

    def get_module(self, obj):
        module_name = obj.module
        map_module_name = settings.API_MODEL_MAP.get(obj.path, None)
        if not module_name and map_module_name:
            return map_module_name
        return module_name
