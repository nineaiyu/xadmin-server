#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
"""系统配置缓存：文件上传 / Office 预览 / 存储后端域（自 system_conf.py 按域拆出）。

经 system_conf.BaseConfCache 组装；默认值单一来源与键说明见 system_conf.py。
"""

from server.const import CONFIG

from .base import ConfigCacheBase


class UploadConfMixin(ConfigCacheBase):
    """文件上传 / Office 预览 / 对象存储配置。"""

    @property
    def FILE_UPLOAD_SIZE(self):
        return self.get_value("FILE_UPLOAD_SIZE", CONFIG.FILE_UPLOAD_SIZE)

    @property
    def PICTURE_UPLOAD_SIZE(self):
        return self.get_value("PICTURE_UPLOAD_SIZE", CONFIG.PICTURE_UPLOAD_SIZE)

    @property
    def FILE_OFFICE_PREVIEW_ENABLED(self):
        """Office 在线预览开关（默认开）：关闭或未装 LibreOffice 时按不支持降级。"""
        return self.get_value("FILE_OFFICE_PREVIEW_ENABLED", CONFIG.FILE_OFFICE_PREVIEW_ENABLED)

    @property
    def FILE_OFFICE_MAX_BYTES(self):
        """Office 预览转换大小上限（字节，默认 20MB）：超过不转换（保护转换进程）。"""
        return int(self.get_value("FILE_OFFICE_MAX_BYTES", CONFIG.FILE_OFFICE_MAX_BYTES))

    @property
    def FILE_OFFICE_CONVERT_TIMEOUT(self):
        """LibreOffice 单次转换超时（秒，默认 60）：超时杀进程并降级为不可预览。"""
        return int(self.get_value("FILE_OFFICE_CONVERT_TIMEOUT", CONFIG.FILE_OFFICE_CONVERT_TIMEOUT))

    @property
    def FILE_OFFICE_WAIT_SECONDS(self):
        """请求侧等待转换产物的窗口（秒，默认 8）：未等到返回 1006 由前端重试。"""
        return int(self.get_value("FILE_OFFICE_WAIT_SECONDS", CONFIG.FILE_OFFICE_WAIT_SECONDS))

    @property
    def FILE_OFFICE_SOFFICE_BIN(self):
        """LibreOffice 可执行文件路径（默认空 = 自动探测 PATH 与常见安装路径）。"""
        return self.get_value("FILE_OFFICE_SOFFICE_BIN", CONFIG.FILE_OFFICE_SOFFICE_BIN)

    @property
    def FILE_STORAGE_QUOTA_MB(self):
        """个人文件存储配额（MB，默认 0 = 不限）：上传前按 creator 聚合校验。"""
        return int(self.get_value("FILE_STORAGE_QUOTA_MB", CONFIG.FILE_STORAGE_QUOTA_MB))

    @property
    def FILE_KEEP_DAYS(self):
        """正式上传文件保留天数（默认 0 = 不清理）。

        仅清理「非临时、无业务引用」的历史文件；物理文件删除由磁盘引用守护兜底。
        """
        return int(self.get_value("FILE_KEEP_DAYS", CONFIG.FILE_KEEP_DAYS))

    @property
    def FILE_UPLOAD_COUNT_LIMIT(self):
        """个人上传文件数量上限（默认 0 = 不限）：上传前按 creator 计数校验。"""
        return int(self.get_value("FILE_UPLOAD_COUNT_LIMIT", CONFIG.FILE_UPLOAD_COUNT_LIMIT))

    @property
    def FILE_PREVIEW_TEXT_MAX_BYTES(self):
        """文本预览读取上限（字节，默认 256KB）：超过即截断并提示下载查看。

        避免把一个几百 MB 的日志整份读进内存再回给浏览器。
        """
        return int(self.get_value("FILE_PREVIEW_TEXT_MAX_BYTES", CONFIG.FILE_PREVIEW_TEXT_MAX_BYTES))

    @property
    def FILE_PREVIEW_THUMB_WIDTH(self):
        """列表缩略图宽度（像素，默认 240）：按原图比例等比缩放，不拉伸。"""
        return int(self.get_value("FILE_PREVIEW_THUMB_WIDTH", CONFIG.FILE_PREVIEW_THUMB_WIDTH))

    @property
    def FILE_PREVIEW_IMAGE_WIDTH(self):
        """抽屉大图宽度（像素，默认 1280）：原图更小时不放大。"""
        return int(self.get_value("FILE_PREVIEW_IMAGE_WIDTH", CONFIG.FILE_PREVIEW_IMAGE_WIDTH))

    @property
    def FILE_PREVIEW_CACHE_KEEP_DAYS(self):
        """预览缓存保留天数（默认 7）：缓存是派生产物，过期删除后按需重建。"""
        return int(self.get_value("FILE_PREVIEW_CACHE_KEEP_DAYS", CONFIG.FILE_PREVIEW_CACHE_KEEP_DAYS))

    @property
    def FILE_STORAGE_BACKEND(self):
        """文件存储后端（默认 local = 本地磁盘）：local / s3 / mirror（搬迁窗口双写，声明式可插拔）。"""
        return self.get_value("FILE_STORAGE_BACKEND", CONFIG.FILE_STORAGE_BACKEND)

    @property
    def FILE_S3_ENDPOINT(self):
        """S3 兼容对象存储端点（默认空 = 按区域使用默认端点，如 AWS）。"""
        return self.get_value("FILE_S3_ENDPOINT", CONFIG.FILE_S3_ENDPOINT)

    @property
    def FILE_S3_BUCKET(self):
        """对象存储桶名（backend=s3 时必填，缺省回退本地）。"""
        return self.get_value("FILE_S3_BUCKET", CONFIG.FILE_S3_BUCKET)

    @property
    def FILE_S3_ACCESS_KEY(self):
        """对象存储 access key（敏感值，落库经 signer 加密）。"""
        return self.get_value("FILE_S3_ACCESS_KEY", CONFIG.FILE_S3_ACCESS_KEY)

    @property
    def FILE_S3_SECRET_KEY(self):
        """对象存储 secret key（敏感值，落库经 signer 加密）。"""
        return self.get_value("FILE_S3_SECRET_KEY", CONFIG.FILE_S3_SECRET_KEY)

    @property
    def FILE_S3_REGION(self):
        """对象存储区域（默认空 = 由 SDK / 端点决定）。"""
        return self.get_value("FILE_S3_REGION", CONFIG.FILE_S3_REGION)

    @property
    def FILE_S3_CUSTOM_DOMAIN(self):
        """对象存储访问域名（CDN / 公开桶；非空时文件 URL 不签名）。"""
        return self.get_value("FILE_S3_CUSTOM_DOMAIN", CONFIG.FILE_S3_CUSTOM_DOMAIN)

    @property
    def FILE_S3_ADDRESSING_STYLE(self):
        """S3 寻址风格（path / virtual；空 = 由 SDK 决定，MinIO 常需 path）。"""
        return self.get_value("FILE_S3_ADDRESSING_STYLE", CONFIG.FILE_S3_ADDRESSING_STYLE)
