#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : block_ip
# author : ly_13
# date : 8/12/2024
import socket
import struct

from django.conf import settings
from django.core.cache import cache

from common.core.modelset import ListDeleteModelSet
from settings.models import Setting
from settings.serializers.security import SecurityBlockIPSerializer
from settings.utils.security import LoginIpBlockUtil


class FilterIps(list):

    def filter(self, pk__in=None):
        if pk__in is None:
            pk__in = []
        return [obj.get('ip') for obj in self.__iter__() if obj.get('pk')() in pk__in]


class IpUtils(object):
    def __init__(self, ip):
        self.ip = ip

    def ip_to_int(self):
        return str(struct.unpack("!I", socket.inet_aton(self.ip))[0])

    def int_to_ip(self):
        return socket.inet_ntoa(struct.pack("!I", int(self.ip)))


class SecurityBlockIpViewSet(ListDeleteModelSet):
    """Ip拦截名单"""
    serializer_class = SecurityBlockIPSerializer
    queryset = Setting.objects.none()

    def filter_queryset(self, obj):
        # 为啥写函数，去没有加(), 因为只有在序列化的时候，才会判断，如果是方法就执行，减少资源浪费
        data = [{'ip': ip, 'pk': IpUtils(ip).ip_to_int, 'created_time': LoginIpBlockUtil(ip).get_block_info} for ip in
                obj]
        return FilterIps(data)

    def get_queryset(self):
        ips = []
        prefix = LoginIpBlockUtil.BLOCK_KEY_TMPL.replace('{}', '')
        keys = cache.keys(f'{prefix}*')
        for key in keys:
            ips.append(key.replace(prefix, ''))

        white_list = settings.SECURITY_LOGIN_IP_WHITE_LIST
        ips = list(set(ips) - set(white_list))
        ips = [ip for ip in ips if ip != '*']
        return ips

    def get_object(self):
        return IpUtils(self.kwargs.get("pk")).int_to_ip()

    def perform_destroy(self, ip):
        LoginIpBlockUtil(ip).clean_block_if_need()
        return 1, 1
