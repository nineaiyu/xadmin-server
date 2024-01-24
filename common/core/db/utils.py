#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : utils
# author : ly_13
# date : 12/18/2023
import ipaddress
import re

from django.db.models import Q


class RelatedManager:
    def __init__(self, instance, field):
        self.instance = instance
        self.field = field
        self.value = None

    def set(self, value):
        self.value = value
        self.instance.__dict__[self.field.name] = value

    @staticmethod
    def get_ip_in_q(name, val):
        q = Q()
        if isinstance(val, str):
            val = [val]
        if ['*'] in val:
            return Q()
        for ip in val:
            if not ip:
                continue
            try:
                if '/' in ip:
                    network = ipaddress.ip_network(ip)
                    ips = network.hosts()
                    q |= Q(**{"{}__in".format(name): ips})
                elif '-' in ip:
                    start_ip, end_ip = ip.split('-')
                    start_ip = ipaddress.ip_address(start_ip)
                    end_ip = ipaddress.ip_address(end_ip)
                    q |= Q(**{"{}__range".format(name): (start_ip, end_ip)})
                elif len(ip.split('.')) == 4:
                    q |= Q(**{"{}__exact".format(name): ip})
                else:
                    q |= Q(**{"{}__startswith".format(name): ip})
            except ValueError:
                continue
        return q

    @classmethod
    def get_filter_attrs_qs(cls, rules):
        filters = []
        for attr in rules:
            if not isinstance(attr, dict):
                continue

            name = attr.get('field')
            val = attr.get('value')
            match = attr.get('match', 'exact')
            if name is None or val is None:
                continue

            if match == 'all':
                filters.append(Q())
                continue

            if match == 'ip_in':
                q = cls.get_ip_in_q(name, val)
            elif match in ("contains", "startswith", "endswith", "gte", "lte", "gt", "lt"):
                lookup = "{}__{}".format(name, match)
                q = Q(**{lookup: val})
            elif match == 'regex':
                try:
                    re.compile(val)
                    lookup = "{}__{}".format(name, match)
                    q = Q(**{lookup: val})
                except re.error:
                    q = Q(pk__isnull=True)
            # elif match == "not":
            #     q = ~Q(**{name: val})
            elif match.startswith('m2m'):
                if not isinstance(val, list):
                    val = [val]
                if match == 'm2m_all':
                    for v in val:
                        filters.append(Q(**{"{}__in".format(name): [v]}))
                    continue
                else:
                    q = Q(**{"{}__in".format(name): val})
            elif match == 'in':
                if not isinstance(val, list):
                    val = [val]
                q = Q() if '*' in val else Q(**{"{}__in".format(name): val})
            else:
                q = Q() if val == '*' else Q(**{name: val})
            if attr.get('exclude'):
                q = ~q
            filters.append(q)
        return filters
