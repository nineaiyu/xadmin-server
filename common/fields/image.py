#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : image
# author : ly_13
# date : 1/17/2024
import os

from django.core.files.storage import default_storage, FileSystemStorage
from django.db import models
from django.db.models.fields.files import ImageFieldFile
from imagekit.cachefiles import ImageCacheFile
from imagekit.models.fields import SpecHostField
from imagekit.specs import SpecHost
from imagekit.utils import generate
from pilkit.processors import ResizeToFill
from pilkit.utils import suggest_extension


def source_name(generator, index):
    source_filename = getattr(generator.source, 'name', None)
    ext = suggest_extension(source_filename or '', generator.format)
    return f"{os.path.splitext(source_filename)[0]}_{index}{ext}"


def get_thumbnail(source, index, force=False):
    scales = source.field.scales
    # spec = ImageSpec(source)
    spec = source.field.get_spec(source=source)
    width = spec.processors[0].width
    height = spec.processors[0].height
    spec.format = 'JPEG'
    spec.options = {'quality': 90}
    if index not in scales:
        index = scales[-1]
    spec.processors = [ResizeToFill(int(width / index), int(height / index))]
    file = ImageCacheFile(spec, name=source_name(spec, index))
    file.generate(force=force)
    return file.name


class ProcessedImageFieldFile(ImageFieldFile):
    is_local_storage = isinstance(default_storage, FileSystemStorage)

    def save(self, name, content, save=True):
        filename, ext = os.path.splitext(name)
        spec = self.field.get_spec(source=content)
        ext = suggest_extension(name, spec.format)
        new_name = '%s%s' % (filename, ext)
        content = generate(spec)
        return super().save(new_name, content, save)

    def delete(self, save=True):
        # Clear the image dimensions cache
        if hasattr(self, "_dimensions_cache"):
            del self._dimensions_cache
        name = self.name
        if self.is_local_storage:
            try:
                for i in self.field.scales:
                    self.name = f"{name.split('.')[0]}_{i}.jpg"
                    super().delete(False)
            except Exception as e:
                pass
        self.name = name
        super().delete(save)

    @property
    def url(self):
        url: str = super().url
        if self.is_local_storage and url.endswith('.png'):
            return url.replace('.png', '_1.jpg')
        return url


class ProcessedImageField(models.ImageField, SpecHostField):
    """
    ProcessedImageField is an ImageField that runs processors on the uploaded
    image *before* saving it to storage. This is in contrast to specs, which
    maintain the original. Useful for coercing fileformats or keeping images
    within a reasonable size.

    """
    attr_class = ProcessedImageFieldFile

    def __init__(self, processors=None, format=None, options=None, scales=None,
                 verbose_name=None, name=None, width_field=None, height_field=None,
                 autoconvert=None, spec=None, spec_id=None, **kwargs):
        """
        The ProcessedImageField constructor accepts all of the arguments that
        the :class:`django.db.models.ImageField` constructor accepts, as well
        as the ``processors``, ``format``, and ``options`` arguments of
        :class:`imagekit.models.ImageSpecField`.

        """
        # if spec is not provided then autoconvert will be True by default
        if spec is None and autoconvert is None:
            autoconvert = True

        self.scales = scales if scales is not None else [1]
        self.format = format if format else 'png'

        SpecHost.__init__(self, processors=processors, format=self.format,
                          options=options, autoconvert=autoconvert, spec=spec,
                          spec_id=spec_id)
        models.ImageField.__init__(self, verbose_name, name, width_field,
                                   height_field, **kwargs)

    def contribute_to_class(self, cls, name):
        self._set_spec_id(cls, name)
        return super().contribute_to_class(cls, name)
