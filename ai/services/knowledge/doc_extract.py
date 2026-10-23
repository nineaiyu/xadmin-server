#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库上传文档解析：PDF / DOCX 二进制 → 纯文本（7.3 AI 演进）。

设计口径：
- **解析只发生在上传写入侧**：提取出的纯文本存 DB（与既有 Markdown 上传同构，
  检索/分块链路零改动——``AiKnowledgeDocument.content`` 仍是文本）；
- **延迟导入**：pypdf / python-docx 在函数内 import——解析失败（依赖缺失/坏文件）
  抛可读 ValidationError，不拖垮模块加载；
- **体积边界**：二进制解码后 2MB 封顶、提取文本按上传文本同口径
  ``MAX_UPLOAD_CONTENT_LENGTH`` 截断校验（调用方负责）；
- 扫描件（图片型 PDF 无文本层）提取为空 → 可读报错，不入库空文档。
"""

import io

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

#: 上传二进制解码后体积上限（bytes）——PDF/DOCX 2MB 已覆盖手册级文档
MAX_UPLOAD_FILE_BYTES = 2 * 1024 * 1024

#: 支持解析的二进制文档类型（扩展名 → 解析器标识）
PARSABLE_EXTENSIONS = {"pdf": "pdf", "docx": "docx"}

#: 纯文本类扩展名（浏览器直接读文本，与既有链路同构）
TEXT_EXTENSIONS = (".md", ".markdown", ".txt")


def file_extension(name: str) -> str:
    """小写扩展名（不含点；无扩展名返回空串）。"""
    text = str(name or "")
    if "." not in text:
        return ""
    return text.rsplit(".", 1)[-1].lower()


def extract_text(extension: str, raw: bytes) -> str:
    """按扩展名解析二进制文档为纯文本；不支持/解析失败/无文本 → 可读 ValidationError。"""
    if extension == "pdf":
        return _extract_pdf(raw)
    if extension == "docx":
        return _extract_docx(raw)
    raise ValidationError(str(_("Unsupported document type: {} (supported: pdf, docx)").format(extension or "-")))


def _require_size(raw: bytes) -> None:
    if not raw:
        raise ValidationError(str(_("The uploaded document is empty")))
    if len(raw) > MAX_UPLOAD_FILE_BYTES:
        raise ValidationError(str(_("The uploaded document exceeds the 2 MB size limit")))


def _extract_pdf(raw: bytes) -> str:
    """PDF 文本层提取（逐页拼接；页间换行）。"""
    _require_size(raw)
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except ValidationError:
        raise
    except Exception as exc:  # noqa: BLE001 坏文件/加密件 → 可读报错（不落日志原文）
        raise ValidationError(str(_("Failed to parse the PDF document, please verify the file"))) from exc
    text = "\n\n".join(page.strip() for page in pages if page and page.strip())
    if not text.strip():
        raise ValidationError(str(_("No extractable text found in the PDF document (it may be a scanned copy)")))
    return text


def _extract_docx(raw: bytes) -> str:
    """DOCX 正文提取：段落 + 表格单元格（各自成行，保序）。"""
    _require_size(raw)
    try:
        import docx

        document = docx.Document(io.BytesIO(raw))
        lines = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                lines.append("\t".join(cell.text for cell in row.cells))
    except ValidationError:
        raise
    except Exception as exc:  # noqa: BLE001 坏文件/旧版二进制 .doc → 可读报错
        raise ValidationError(str(_("Failed to parse the DOCX document, please verify the file"))) from exc
    text = "\n".join(line.strip() for line in lines if line and line.strip())
    if not text.strip():
        raise ValidationError(str(_("No extractable text found in the DOCX document")))
    return text
