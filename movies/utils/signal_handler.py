#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : signal_handler
# author : ly_13
# date : 12/5/2023
import logging

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from common.base.magic import cache_response
from movies.models import EpisodeInfo, FilmInfo, Category, SwipeInfo

logger = logging.getLogger(__name__)


@receiver([post_save, post_delete])
def clean_cache_handler(sender, **kwargs):
    if issubclass(sender, EpisodeInfo):
        if kwargs.get('update_fields') == {'views'}:
            return
        cache_response.invalid_cache('H5FilmDetailView_get_*')
        logger.info(f"invalid cache {sender}")
    elif issubclass(sender, FilmInfo):
        if kwargs.get('update_fields') == {'views'}:
            return
        cache_response.invalid_cache('HomeView_get')
        cache_response.invalid_cache('H5FilmActorDetailView_get_*')
        cache_response.invalid_cache('H5FilmView_list_*')
        logger.info(f"invalid cache {sender}")
    elif issubclass(sender, Category):
        cache_response.invalid_cache('HomeView_get')
        cache_response.invalid_cache('H5FilmFilterView_get')
        logger.info(f"invalid cache {sender}")
    elif issubclass(sender, SwipeInfo):
        cache_response.invalid_cache('HomeView_get')
        logger.info(f"invalid cache {sender}")
