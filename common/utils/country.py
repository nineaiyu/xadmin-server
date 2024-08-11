#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : country
# author : ly_13
# date : 8/6/2024
import gettext

import phonenumbers
import pycountry
from django.utils.translation import gettext_lazy as _
from phonenumbers import PhoneMetadata


def get_country_phone_codes():
    phone_codes = []
    for region_code in phonenumbers.SUPPORTED_REGIONS:
        phone_metadata = PhoneMetadata.metadata_for_region(region_code)
        if phone_metadata:
            phone_codes.append((region_code, phone_metadata.country_code))
    return phone_codes


def get_country(region_code):
    country = pycountry.countries.get(alpha_2=region_code)
    if country:
        return country
    else:
        return None


def get_country_phone_choices(locales=None):
    codes = get_country_phone_codes()
    choices = []
    german = None
    if locales:
        german = gettext.translation(
            "iso3166-1", pycountry.LOCALES_DIR, languages=[locales]
        )
    for code, phone in codes:
        country = get_country(code)
        if not country:
            continue
        country_name = country.name
        flag = country.flag

        if country.name == 'China':
            country_name = _('China')

        if code == 'TW':
            country_name = 'Taiwan'
            flag = get_country('CN').flag
        choices.append({
            'name': german.gettext(country_name) if german else country_name,
            'phone_code': f'+{phone}',
            'flag': flag,
            'code': code,
        })

    choices.sort(key=lambda x: x['name'])
    return choices


COUNTRY_CALLING_CODES = get_country_phone_choices()
COUNTRY_CALLING_CODES_ZH = get_country_phone_choices("zh")
