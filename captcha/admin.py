# Register your models here.
from django.contrib import admin

from captcha.models import CaptchaStore

admin.register(CaptchaStore)
