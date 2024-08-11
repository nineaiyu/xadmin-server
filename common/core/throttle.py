#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : throttle
# author : ly_13
# date : 6/2/2023


from rest_framework.throttling import UserRateThrottle, AnonRateThrottle


class RegisterThrottle(AnonRateThrottle):
    scope = "register"


class ResetPasswordThrottle(AnonRateThrottle):
    scope = "reset_password"


class LoginThrottle(AnonRateThrottle):
    scope = "login"


class UploadThrottle(UserRateThrottle):
    """上传速率限制"""
    scope = "upload"


class Download1Throttle(UserRateThrottle):
    """下载速率限制"""
    scope = "download1"


class Download2Throttle(UserRateThrottle):
    """下载速率限制"""
    scope = "download2"
