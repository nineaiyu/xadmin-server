# -*- coding: utf-8 -*-
#
import os

import ipdb
from django.conf import settings

__all__ = ["get_ip_city_by_ipip"]
ipip_db = None


def init_ipip_db():
    global ipip_db
    if ipip_db is not None:
        return

    ipip_db_path = os.path.join(settings.DATA_DIR, "system", "ipipfree.ipdb")
    if not os.path.exists(ipip_db_path):
        ipip_db_path = os.path.join(os.path.dirname(__file__), "ipipfree.ipdb")
    if not os.path.exists(ipip_db_path):
        raise FileNotFoundError("IP Database not found, please run `python manage.py download_ip_db`")
    ipip_db = ipdb.City(ipip_db_path)


def get_ip_city_by_ipip(ip):
    try:
        init_ipip_db()
    except Exception:
        # IP 库初始化失败：返回 None（保持「查不到」语义，调用方已判空）
        return None
    if ipip_db is None:  # 初始化失败时保持原有「查不到返回 None」语义
        return None
    try:
        info = ipip_db.find_info(ip, "CN")
    except ValueError:
        return None
    if not info:
        return None
    return {"city": info.city_name, "country": info.country_name}
