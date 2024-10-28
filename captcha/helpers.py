import random
import re

from PIL import Image
from django.conf import settings
from django.urls import reverse


def _callable_from_string(string_or_callable):
    if callable(string_or_callable):
        return string_or_callable
    else:
        return getattr(
            __import__(".".join(string_or_callable.split(".")[:-1]), {}, {}, [""]),
            string_or_callable.split(".")[-1],
        )


def get_challenge(generator=None):
    return _callable_from_string(generator or settings.CAPTCHA_CHALLENGE_FUNCT)


def noise_functions():
    if settings.CAPTCHA_NOISE_FUNCTIONS:
        return map(_callable_from_string, settings.CAPTCHA_NOISE_FUNCTIONS)
    return []


def filter_functions():
    if settings.CAPTCHA_FILTER_FUNCTIONS:
        return map(_callable_from_string, settings.CAPTCHA_FILTER_FUNCTIONS)
    return []


def math_challenge():
    operators = ("+", "*", "-")
    operands = (random.randint(1, 10), random.randint(1, 10))
    operator = random.choice(operators)
    if operands[0] < operands[1] and "-" == operator:
        operands = (operands[1], operands[0])
    challenge = "%d%s%d" % (operands[0], operator, operands[1])
    return (
        "{}=".format(challenge.replace("*", settings.CAPTCHA_MATH_CHALLENGE_OPERATOR)),
        str(eval(challenge)),
    )


def random_char_challenge():
    chars, ret = "abcdefghijklmnopqrstuvwxyz", ""
    for i in range(settings.CAPTCHA_LENGTH):
        ret += random.choice(chars)
    return ret.upper(), ret


def unicode_challenge():
    chars, ret = "äàáëéèïíîöóòüúù", ""
    for i in range(settings.CAPTCHA_LENGTH):
        ret += random.choice(chars)
    return ret.upper(), ret


def get_format_color(color):
    if color.lower().startswith("rgba"):
        colors = re.findall(r"\d+\.?\d*", color)
        if float(colors[-1]) <= 1:
            colors[-1] = float(colors[-1]) * 255
        color = tuple(map(int, colors))
    return color


def makeimg(size, color):
    if color == "transparent":
        image = Image.new("RGBA", size)
    else:
        if color.lower().startswith("rgba"):
            image = Image.new("RGBA", size, get_format_color(color))
        else:
            image = Image.new("RGB", size, color)
    return image


def noise_arcs(draw, image):
    size = image.size
    color = get_format_color(settings.CAPTCHA_FOREGROUND_COLOR)
    draw.arc([-20, -20, size[0], 20], 0, 295, fill=color)
    draw.line(
        [-20, 20, size[0] + 20, size[1] - 20], fill=color
    )
    draw.line([-20, 0, size[0] + 20, size[1]], fill=color)
    return draw


def noise_dots(draw, image):
    size = image.size
    for p in range(int(size[0] * size[1] * 0.1)):
        draw.point(
            (random.randint(0, size[0]), random.randint(0, size[1])),
            fill=get_format_color(settings.CAPTCHA_FOREGROUND_COLOR),
        )
    return draw


def noise_null(draw, image):
    return draw


def post_smooth(image):
    from PIL import ImageFilter

    return image.filter(ImageFilter.SMOOTH)


def captcha_image_url(key):
    """Return url to image. Need for ajax refresh and, etc"""
    return reverse("system:captcha-image", args=[key])


def captcha_audio_url(key):
    """Return url to image. Need for ajax refresh and, etc"""
    return reverse("system:captcha-audio", args=[key])
