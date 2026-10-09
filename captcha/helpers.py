import operator
import random
import re
from collections.abc import Callable, Iterable
from typing import Any

from django.conf import settings
from django.urls import reverse
from PIL import Image

#: 数学验证码的运算表：显式映射（不用 eval——输入虽全部内部生成，
#: 但静态扫描把 eval 判为红旗，且字典求值语义更清晰）
_MATH_OPERATORS: dict[str, Callable[[int, int], Any]] = {"+": operator.add, "-": operator.sub, "*": operator.mul}


def _callable_from_string(string_or_callable: Any) -> Any:
    if callable(string_or_callable):
        return string_or_callable
    else:
        return getattr(
            __import__(".".join(string_or_callable.split(".")[:-1]), {}, {}, [""]),
            string_or_callable.split(".")[-1],
        )


def get_challenge(generator: Any = None) -> Any:
    return _callable_from_string(generator or settings.CAPTCHA_CHALLENGE_FUNCT)


def noise_functions() -> Iterable[Callable[[Any, Image.Image], Any]]:
    if settings.CAPTCHA_NOISE_FUNCTIONS:
        return map(_callable_from_string, settings.CAPTCHA_NOISE_FUNCTIONS)
    return []


def filter_functions() -> Iterable[Callable[[Image.Image], Image.Image]]:
    if settings.CAPTCHA_FILTER_FUNCTIONS:
        return map(_callable_from_string, settings.CAPTCHA_FILTER_FUNCTIONS)
    return []


def math_challenge() -> tuple[str, str]:
    symbol = random.choice(tuple(_MATH_OPERATORS))
    operands = (random.randint(1, 10), random.randint(1, 10))
    if operands[0] < operands[1] and "-" == symbol:
        operands = (operands[1], operands[0])
    challenge = f"{operands[0]}{symbol}{operands[1]}"
    answer = _MATH_OPERATORS[symbol](*operands)
    return (
        "{}=".format(challenge.replace("*", settings.CAPTCHA_MATH_CHALLENGE_OPERATOR)),
        str(answer),
    )


def random_char_challenge() -> tuple[str, str]:
    chars, ret = "abcdefghijklmnopqrstuvwxyz", ""
    for _ in range(settings.CAPTCHA_LENGTH):
        ret += random.choice(chars)
    return ret.upper(), ret


def unicode_challenge() -> tuple[str, str]:
    chars, ret = "äàáëéèïíîöóòüúù", ""
    for _ in range(settings.CAPTCHA_LENGTH):
        ret += random.choice(chars)
    return ret.upper(), ret


def get_format_color(color: str) -> str | tuple[int, ...]:
    if color.lower().startswith("rgba"):
        colors = re.findall(r"\d+\.?\d*", color)
        if float(colors[-1]) <= 1:
            colors[-1] = float(colors[-1]) * 255
        return tuple(map(int, colors))
    return color


def makeimg(size: tuple[int, int], color: Any) -> Image.Image:
    if color == "transparent":
        image = Image.new("RGBA", size)
    else:
        if color.lower().startswith("rgba"):
            image = Image.new("RGBA", size, get_format_color(color))
        else:
            image = Image.new("RGB", size, color)
    return image


def noise_arcs(draw: Any, image: Image.Image) -> Any:
    size = image.size
    color = get_format_color(settings.CAPTCHA_FOREGROUND_COLOR)
    draw.arc([-20, -20, size[0], 20], 0, 295, fill=color)
    draw.line([-20, 20, size[0] + 20, size[1] - 20], fill=color)
    draw.line([-20, 0, size[0] + 20, size[1]], fill=color)
    return draw


def noise_dots(draw: Any, image: Image.Image) -> Any:
    size = image.size
    for _ in range(int(size[0] * size[1] * 0.1)):
        draw.point(
            (random.randint(0, size[0]), random.randint(0, size[1])),
            fill=get_format_color(settings.CAPTCHA_FOREGROUND_COLOR),
        )
    return draw


def noise_null(draw: Any, image: Image.Image) -> Any:
    return draw


def post_smooth(image: Image.Image) -> Image.Image:
    from PIL import ImageFilter

    return image.filter(ImageFilter.SMOOTH)


def captcha_image_url(key: str) -> str:
    """Return url to image. Need for ajax refresh and, etc"""
    url: str = reverse("system:captcha-image", args=[key])
    return url


def captcha_audio_url(key: str) -> str:
    """Return url to image. Need for ajax refresh and, etc"""
    url: str = reverse("system:captcha-audio", args=[key])
    return url
