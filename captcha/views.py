import json
import os
import random
import subprocess
import tempfile
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404, HttpResponse
from PIL import Image, ImageDraw, ImageFont
from ranged_response import RangedFileResponse

from captcha.helpers import captcha_audio_url, captcha_image_url, filter_functions, makeimg, noise_functions
from captcha.models import CaptchaStore
from common.core.throttle import allow_by_ip

# Distance of the drawn text from the top of the captcha image
DISTANCE_FROM_TOP = 4

#: 匿名验证码端点的每 IP 限流（次 / 分钟）：取图/刷新都会生成并写入 CaptchaStore 行，
#: 无节流可被刷成灌库；正常登录流程每个会话只取一两次，60 次/分钟只挡滥用
CAPTCHA_IP_LIMIT_PER_MINUTE = 60


def _rate_limited(request, scope: str) -> bool:
    return not allow_by_ip(request, scope=scope, limit=CAPTCHA_IP_LIMIT_PER_MINUTE, window_seconds=60)


def getsize(font, text):
    if hasattr(font, "getbbox"):
        _top, _left, _right, _bottom = font.getbbox(text)
        return _right - _left, _bottom - _top
    elif hasattr(font, "getoffset"):
        return tuple([x + y for x, y in zip(font.getsize(text), font.getoffset(text), strict=True)])
    else:
        return font.getsize(text)


def captcha_image(request, key, scale=1):
    if _rate_limited(request, "captcha_image"):
        return HttpResponse(status=429)
    if scale == 2 and not settings.CAPTCHA_2X_IMAGE:
        raise Http404
    try:
        store = CaptchaStore.objects.get(hashkey=key)
    except CaptchaStore.DoesNotExist:
        # HTTP 410 Gone status so that crawlers don't index these expired urls.
        return HttpResponse(status=410)

    random.seed(key)  # Do not generate different images for the same key
    image = _render_challenge(store.challenge, scale)

    out = BytesIO()
    image.save(out, "PNG")
    out.seek(0)

    response = HttpResponse(content_type="image/png")
    response.write(out.read())
    response["Content-length"] = out.tell()

    # At line :50 above we fixed the random seed so that we always generate the
    # same image, see: https://github.com/mbi/django-simple-captcha/pull/194
    # This is a problem though, because knowledge of the seed will let an attacker
    # predict the next random (globally). We therefore reset the random here.
    # Reported in https://github.com/mbi/django-simple-captcha/pull/221
    random.seed()

    return response


def _resolve_font_path():
    """settings.CAPTCHA_FONT_PATH 归一为单个字体路径（原 captcha_image 内联分支）。"""
    if isinstance(settings.CAPTCHA_FONT_PATH, str):
        return settings.CAPTCHA_FONT_PATH
    if isinstance(settings.CAPTCHA_FONT_PATH, (list, tuple)):
        return random.choice(settings.CAPTCHA_FONT_PATH)
    raise ImproperlyConfigured("settings.CAPTCHA_FONT_PATH needs to be a path to a font or list of paths to fonts")


def _load_font(fontpath, scale):
    """按扩展名装载字体（ttf 走指定字号，其余交 Pillow 默认装载）。"""
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    if fontpath.lower().strip().endswith("ttf"):
        font = ImageFont.truetype(fontpath, settings.CAPTCHA_FONT_SIZE * scale)
    else:
        font = ImageFont.load(fontpath)
    return font


def _split_challenge_chars(text):
    """标点归并到前一个字符（与 django-simple-captcha 原实现同口径）。"""
    charlist: list[str] = []
    for char in text:
        if char in settings.CAPTCHA_PUNCTUATION and len(charlist) >= 1:
            charlist[-1] += char
        else:
            charlist.append(char)
    return charlist


def _render_challenge(text, scale):
    """绘制挑战文本：尺寸解析 → 逐字符合成 → 居中裁剪 → 噪声与滤镜。

    随机序列由调用方按 key 播种（同 key 同图），本函数只做确定性绘制。
    """
    font = _load_font(_resolve_font_path(), scale)
    if settings.CAPTCHA_IMAGE_SIZE:
        size = settings.CAPTCHA_IMAGE_SIZE
    else:
        size = getsize(font, text)
        size = (size[0] * 2, int(size[1] * 1.4))

    image = makeimg(size, settings.CAPTCHA_BACKGROUND_COLOR)
    xpos = 2

    charlist = _split_challenge_chars(text)
    for char in charlist:
        fgimage = makeimg(size, settings.CAPTCHA_FOREGROUND_COLOR)
        charimage = Image.new("L", getsize(font, f" {char} "), "#000000")
        chardraw = ImageDraw.Draw(charimage)
        chardraw.text((0, 0), f" {char} ", font=font, fill="#ffffff")
        if settings.CAPTCHA_LETTER_ROTATION:
            charimage = charimage.rotate(
                random.randrange(*settings.CAPTCHA_LETTER_ROTATION),
                expand=0,
                resample=Image.Resampling.BICUBIC,
            )
        charimage = charimage.crop(charimage.getbbox())
        maskimage = Image.new("L", size)

        maskimage.paste(
            charimage,
            (
                xpos,
                DISTANCE_FROM_TOP,
                xpos + charimage.size[0],
                DISTANCE_FROM_TOP + charimage.size[1],
            ),
        )
        size = maskimage.size
        image = Image.composite(fgimage, image, maskimage)
        xpos = xpos + 2 + charimage.size[0]

    if settings.CAPTCHA_IMAGE_SIZE:
        # centering captcha on the image
        tmpimg = makeimg(size, settings.CAPTCHA_BACKGROUND_COLOR)
        tmpimg.paste(
            image,
            (
                int((size[0] - xpos) / 2),
                int((size[1] - charimage.size[1]) / 2 - DISTANCE_FROM_TOP),
            ),
        )
        image = tmpimg.crop((0, 0, size[0], size[1]))
    else:
        image = image.crop((0, 0, xpos + 1, size[1]))
    draw = ImageDraw.Draw(image)

    for f in noise_functions():
        draw = f(draw, image)
    for f in filter_functions():
        image = f(image)
    return image


def captcha_audio(request, key):
    if _rate_limited(request, "captcha_audio"):
        return HttpResponse(status=429)
    if settings.CAPTCHA_FLITE_PATH:
        try:
            store = CaptchaStore.objects.get(hashkey=key)
        except CaptchaStore.DoesNotExist:
            # HTTP 410 Gone status so that crawlers don't index these expired urls.
            return HttpResponse(status=410)

        text = store.challenge
        if "captcha.helpers.math_challenge" == settings.CAPTCHA_CHALLENGE_FUNCT:
            text = text.replace("*", "times").replace("-", "minus").replace("+", "plus")
        else:
            text = ", ".join(list(text))
        path = str(os.path.join(tempfile.gettempdir(), f"{key}.wav"))
        subprocess.call([settings.CAPTCHA_FLITE_PATH, "-t", text, "-o", path])

        # Add arbitrary noise if sox is installed
        if settings.CAPTCHA_SOX_PATH:
            arbnoisepath = str(os.path.join(tempfile.gettempdir(), "%s_arbitrary.wav") % key)
            mergedpath = str(os.path.join(tempfile.gettempdir(), "%s_merged.wav") % key)
            subprocess.call(
                [
                    settings.CAPTCHA_SOX_PATH,
                    "-r",
                    "8000",
                    "-n",
                    arbnoisepath,
                    "synth",
                    "2",
                    "brownnoise",
                    "gain",
                    "-15",
                ]
            )
            subprocess.call(
                [
                    settings.CAPTCHA_SOX_PATH,
                    "-m",
                    arbnoisepath,
                    path,
                    "-t",
                    "wavpcm",
                    "-b",
                    "16",
                    mergedpath,
                ]
            )
            os.remove(arbnoisepath)
            os.remove(path)
            os.rename(mergedpath, path)

        if os.path.isfile(path):
            response = RangedFileResponse(request, open(path, "rb"), content_type="audio/wav")
            response["Content-Disposition"] = f'attachment; filename="{key}.wav"'
            return response
    raise Http404


def captcha_refresh(request):
    """Return json with new captcha for ajax refresh request"""
    if _rate_limited(request, "captcha_refresh"):
        return HttpResponse(status=429)
    if not request.headers.get("x-requested-with") == "XMLHttpRequest":
        raise Http404

    new_key = CaptchaStore.pick()
    to_json_response = {
        "key": new_key,
        "image_url": captcha_image_url(new_key),
        "audio_url": captcha_audio_url(new_key) if settings.CAPTCHA_FLITE_PATH else None,
    }
    return HttpResponse(json.dumps(to_json_response), content_type="application/json")
