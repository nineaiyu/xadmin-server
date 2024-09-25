# -*- coding: utf-8 -*-
#
import os

import ipdb

__all__ = ['get_ip_city_by_ipip']
ipip_db = None


def get_ip_city_by_ipip(ip):
    global ipip_db
    try:
        if ipip_db is None:
            ipip_db_path = os.path.join(os.path.dirname(__file__), 'ipipfree.ipdb')
            ipip_db = ipdb.City(ipip_db_path)
        info = ipip_db.find_info(ip, 'CN')
    except ValueError:
        return None
    except Exception:
        return None
    if not info:
        raise None
    return {'city': info.city_name, 'country': info.country_name}
