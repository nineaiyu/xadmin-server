#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : utils
# author : ly_13
# date : 6/2/2023
import base64
import hashlib
import logging
import os

from Cryptodome import Random
from Cryptodome.Cipher import AES

logger = logging.getLogger(__file__)


class AESCipher(object):

    def __init__(self, key):
        self.key = hashlib.sha256(key.encode()).digest()

    def encrypt(self, raw):
        raw = self._pack_data(raw)
        iv = Random.new().read(AES.block_size)
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        return base64.b64encode(iv + cipher.encrypt(raw))

    def decrypt(self, enc):
        enc = base64.b64decode(enc)
        iv = enc[:AES.block_size]
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        return self._unpack_data(cipher.decrypt(enc[AES.block_size:]))

    @staticmethod
    def _pack_data(s):
        return s + ((AES.block_size - len(s) % AES.block_size) * chr(AES.block_size - len(s) % AES.block_size)).encode(
            'utf-8')

    @staticmethod
    def _unpack_data(s):
        data = s[:-ord(s[len(s) - 1:])]
        if isinstance(data, bytes):
            data = data.decode('utf-8')
        return data


class AesBaseCrypt(object):

    def __init__(self):
        self.cipher = AESCipher(self.__class__.__name__)

    def set_encrypt_uid(self, key):
        return self.cipher.encrypt(key.encode('utf-8')).decode('utf-8')

    def get_decrypt_uid(self, enc):
        try:
            return self.cipher.decrypt(enc)
        except Exception as e:
            logger.warning(f'decrypt {enc} failed. exception:{e}')


def get_choices_dict(choices, disabled_choices=None):
    result = []
    choices_org_list = list(choices)
    for choice in choices_org_list:
        val = {'key': choice[0], 'label': choice[1], 'disabled': False}
        if disabled_choices and isinstance(disabled_choices, list) and choice[0] in disabled_choices:
            val['disabled'] = True
        result.append(val)
    return result


def get_choices_name_from_key(choices, key):
    choices_org_list = list(choices)
    for choice in choices_org_list:
        if choice[0] == key:
            return choice[1]
    return ''


def redis_key_func(key, key_prefix, version):
    """
    Default function to generate keys.

    Construct the key used by all other methods. By default, prepend
    the `key_prefix`. KEY_FUNCTION can be used to specify an alternate
    function with custom key making behavior.
    """
    return key


def redis_reverse_key_func(key: str) -> str:
    return key


def menu_list_to_tree(data: list, root_field: str = 'parent') -> list:
    """
    将权限菜单转换为树状结构
    """
    mapping: dict = dict(zip([str(i['pk']) for i in data], data))

    # 树容器
    container: list = []

    for d in data:
        # 如果找不到父级项，则是根节点
        parent: dict = mapping.get(str(d.get(root_field)))
        if parent is None:
            container.append(d)
        else:
            children: list = parent.get('children')
            if not children:
                children = []
            children.append(d)
            parent.update({'children': children, 'count': len(children)})
    return container


def format_menu_data(data):
    new_result = []
    for d in data:
        if d.get('count', -1) < 2:
            route = {
                'path': f"/default{d.get('path')}",
                'title': d.get('title'),
                'meta': {**d.get('meta', {})},
                'children': [d]
            }
        else:
            route = d
        new_result.append(route)
    return new_result


def remove_file(name):
    try:
        if os.path.isdir(name):
            os.rmdir(name)
        else:
            os.remove(name)
        logger.info(f"remove {name} success")
    except Exception as e:
        # FileNotFoundError is raised if the file or directory was removed
        # concurrently.
        logger.warning(f"remove {name} failed {e}")
