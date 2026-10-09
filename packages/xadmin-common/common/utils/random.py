#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : random
# author : ly_13
# date : 12/10/2024
import random
import secrets
import socket
import string
import struct
from typing import Any

string_punctuation = "!#$%&()*+,-.:;<=?@[]_~"


def random_datetime(date_start: Any, date_end: Any) -> Any:
    random_delta = (date_end - date_start) * random.random()
    return date_start + random_delta


def random_ip() -> Any:
    return socket.inet_ntoa(struct.pack(">I", random.randint(1, 0xFFFFFFFF)))


def random_replace_char(seq: Any, chars: Any, length: Any) -> Any:
    """把 ``seq`` 中 ``length`` 个随机位置（保留首字符）的字符替换为 ``chars`` 中的随机字符。

    可用位置不足（``length > len(seq) - 1``）时抛 ``ValueError``：调用方（密码生成）
    应显式失败而不是静默降级——此前的实现会在短序列上死循环或抛 ``randbelow`` 内部异常。
    """
    if length <= 0:
        return seq
    candidates = list(range(1, len(seq)))  # 首字符固定保留，故从下标 1 起取样
    if length > len(candidates):
        raise ValueError("The sequence is too short to replace the requested number of characters")
    for index in secrets.SystemRandom().sample(candidates, length):
        seq[index] = secrets.choice(chars)
    return seq


def remove_exclude_char(s: Any, exclude_chars: Any) -> Any:
    for i in exclude_chars:
        s = s.replace(i, "")
    return s


def random_string(
    length: int,
    lower: bool = True,
    upper: bool = True,
    digit: bool = True,
    special_char: bool = False,
    exclude_chars: str = "",
    symbols: Any = string_punctuation,
) -> Any:
    if not any([lower, upper, digit]):
        raise ValueError("At least one of `lower`, `upper`, `digit` must be `True`")
    if length < 4:
        raise ValueError("The length of the string must be greater than 3")

    char_list = []
    if lower:
        lower_chars = remove_exclude_char(string.ascii_lowercase, exclude_chars)
        if not lower_chars:
            raise ValueError("After excluding characters, no lowercase letters are available.")
        char_list.append(lower_chars)

    if upper:
        upper_chars = remove_exclude_char(string.ascii_uppercase, exclude_chars)
        if not upper_chars:
            raise ValueError("After excluding characters, no uppercase letters are available.")
        char_list.append(upper_chars)

    if digit:
        digit_chars = remove_exclude_char(string.digits, exclude_chars)
        if not digit_chars:
            raise ValueError("After excluding characters, no digits are available.")
        char_list.append(digit_chars)

    secret_chars = [secrets.choice(chars) for chars in char_list]

    all_chars = "".join(char_list)

    remaining_length = length - len(secret_chars)
    seq = [secrets.choice(all_chars) for _ in range(remaining_length)]

    if special_char:
        special_chars = remove_exclude_char(symbols, exclude_chars)
        if not special_chars:
            raise ValueError("After excluding characters, no special characters are available.")
        symbol_num = length // 16 + 1
        if symbol_num > len(seq) - 1:
            raise ValueError("The length of the string is too short to include special characters")
        seq = random_replace_char(seq, special_chars, symbol_num)
    secret_chars += seq

    secrets.SystemRandom().shuffle(secret_chars)
    return "".join(secret_chars)
