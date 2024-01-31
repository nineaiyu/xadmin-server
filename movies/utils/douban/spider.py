#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : spider
# author : ly_13
# date : 1/18/2024
import logging
import random
import re
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import requests
from django.core.files.base import ContentFile
from django.utils.datetime_safe import datetime

from movies.models import *
from movies.utils.douban.movie_celebrities_parse import MovieCelebritiesParse
from movies.utils.douban.movie_page_parse import MoviePageParse
from movies.utils.douban.movie_person_page_parse import PersonPageParse

logger = logging.getLogger(__name__)

user_agent = [
    "Mozilla/5.0 (Windows NT 6.1; WOW64; rv:34.0) Gecko/20100101 Firefox/34.0",
    "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/534.57.2 (KHTML, like Gecko) Version/5.1.7 Safari/534.57.2",
    "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/39.0.2171.71 Safari/537.36",
    "Mozilla/5.0 (Windows; U; Windows NT 6.1; en-US) AppleWebKit/534.16 (KHTML, like Gecko) Chrome/10.0.648.133 Safari/534.16",
    "Mozilla/5.0 (Windows NT 6.3; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/78.0.3904.108 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/64.0.3282.140 Safari/537.36 Edge/17.17134",
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/75.0.3770.100 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/74.0.3729.169 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/78.0.3904.108 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/55.0.2883.87 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/74.0.3729.108 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/75.0.3770.100 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.106 Safari/537.36"
]


class DouBanMovieSpider:
    def __init__(self):

        # 请求头
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/71.0.3578.98 Safari/537.36'
        }

        # 请求过期时间
        self.timeout = 20

    def _set_random_sleep_time(self):
        """
        设置随机睡眠时间
        :return:
        """
        # 爬虫间隔时间
        self.sleep_time = random.randint(1, 2)

    def _set_random_ua(self):
        """
        设置随机ua
        :return:
        """
        self.headers['User-Agent'] = user_agent[random.randint(0, len(user_agent) - 1)]

    def get_image_content(self, url):
        res = requests.get(url, headers=self.headers, timeout=self.timeout)
        return ContentFile(BytesIO(res.content).read())

    def get_movie_info(self, movie_id):
        """
        获取当前电影信息
        :param movie_id:
        :return:
        """
        logger.info('开始获取电影' + str(movie_id) + '信息...')
        try:
            self._set_random_ua()
            # self._set_random_sleep_time()
            # time.sleep(self.sleep_time)
            movie_url = 'https://movie.douban.com/subject/' + str(movie_id) + '/'
            movie_info_response = requests.get(movie_url, headers=self.headers, timeout=self.timeout)
            movie_info_html = movie_info_response.text
            movie_page_parse = MoviePageParse(movie_id, movie_info_html)
            movie_info_json = movie_page_parse.parse()
            logger.info('电影' + str(movie_id) + '信息为' + str(movie_info_json))
            return movie_info_json

        except Exception as err:
            logger.error('获取电影' + str(movie_id) + '信息失败' + str(err))

    def get_celebrities_info(self, movie_id):
        logger.info('开始获取电影演职员' + str(movie_id) + '信息...')
        try:
            self._set_random_ua()
            celebrities_url = f'https://movie.douban.com/subject/{movie_id}/celebrities'
            celebrities_url_response = requests.get(celebrities_url, headers=self.headers, timeout=self.timeout)
            movie_page_parse = MovieCelebritiesParse(movie_id, celebrities_url_response.text)
            movie_info_json = movie_page_parse.parse()
            logger.info('电影演职员' + str(movie_id) + '信息为' + str(movie_info_json))
            return movie_info_json
        except Exception as err:
            logger.error('电影演职员' + str(movie_id) + '信息失败' + str(err))

    def get_person_info(self, person_id):
        """
        获取演员信息
        :param person_id:
        :return:
        """
        logger.info('开始获取演员' + str(person_id) + '信息...')
        try:
            self._set_random_ua()
            self._set_random_sleep_time()
            person_url = f'https://movie.douban.com/celebrity/{person_id}/'
            person_info_html = requests.get(person_url, headers=self.headers, timeout=self.timeout).text
            person_page_parse = PersonPageParse(person_id, person_info_html)
            person_info_json = person_page_parse.parse()
            logger.info('获取演员' + str(person_id) + '信息成功')
            logger.info('演员' + str(person_id) + '信息为' + str(person_info_json))
            return person_info_json
        except Exception as err:
            logger.error('获取演员' + str(person_id) + '信息失败')

    def actor_task(self, person, film):
        actor_info = self.get_person_info(person['id'])
        try:
            birthday = datetime.strptime(actor_info['birthday'], '%Y年%m月%d日') if actor_info[
                'birthday'] else datetime.now()
        except Exception as e:
            birthday = datetime.now()
        data = {
            'name': actor_info['name'].split(' ')[0],
            'foreign_name': actor_info['other_english_name'],
            # 'avatar': actor_info['image_url'],
            'sex': 0 if actor_info['gender'] == '男' else 1,
            'birthday': birthday,
            'introduction': actor_info.get('introduction'),
            'birthplace': actor_info.get('birthplace'),
            'profession': actor_info.get('profession')
        }
        if not data['name']:
            return
        instance, _ = ActorInfo.objects.update_or_create(douban=person['id'], defaults=data)
        if not instance.avatar:
            instance.avatar.save('', self.get_image_content(actor_info['image_url']))
            instance.save(update_fields=['avatar'])

        who = person['role']
        if person['role'].startswith('导演 Director'):
            actor_type = 1
        elif person['role'].startswith('编剧 Writer'):
            actor_type = 2
        else:
            actor_type = 3
            who = person['role'].replace('演员 Actor', '').replace('/Actress', '').strip()
            if not who:
                who = person['role']
        ActorShip.objects.update_or_create(actor=instance, film=film, actor_type=actor_type, defaults={'who': who})


def get_film_info(movie_id):
    spider = DouBanMovieSpider()

    """
    获取电影信息和电影演员信息
    :return:
    """
    if not movie_id or len(str(movie_id)) < 6:
        return
    # 获取电影信息
    movie_info = spider.get_movie_info(movie_id)
    if not movie_info['name']:
        return
    """
    person: {'id': '1276086', 'name': '郭帆 Frant Gwo', 'role': '导演 Director'}
    actor_info: {
        'id': '1276086', 'name': '郭帆 Frant Gwo', 'image_url': 'https://img9.doubanio.com/view/personage/raw/public/300a2139dffdb8cd3eba50101766a4e4.jpg', 
        'constellation': ':射手座', 'gender': '男', 'birthday': '1980年12月15日', 'birthplace': '中国,山东,济宁', 'profession': '制片人/导演/演员/编剧/其它', 
        'other_chinese_name': '', 'other_english_name': 'FanGuo', 'family_member': '', 
        'introduction': '郭帆，男，1980年12月15日主任委员'
    }
    film:{'id': 26266893, 'image_url': 'https://img3.doubanio.com/view/photo/s_ratio_poster/public/p2545472803.jpg', 'name': '流浪地球', 'genres': ['科幻', '冒险', '灾难'],
     'countries': ['中国大陆'], 'languages': ['汉语普通话', '英语', '俄语', '法语', '日语', '韩语', '印尼语'], 
     'pubdates': ['2019-02-05(中国大陆)', '2020-11-26(中国大陆重映)'], 'episodes': '1', 'durations': ['125分钟', '137分钟(重映版)'], 
     'other_names': ['流浪地球：飞跃2020特别版', 'TheWanderingEarth', 'TheWanderingEarth:Beyond2020SpecialEdition'], 
     'summary': '近未来，科学家们发现太阳急速衰老膨胀，短改编。', 
     'rating': {'average': '7.9', 'reviews_count': '1948275'}}

    """
    region_list = []
    for region in movie_info['countries']:
        reg, _ = Category.objects.update_or_create(category_type=2, name=region)
        region_list.append(reg)

    languages_list = []
    for region in movie_info['languages']:
        reg, _ = Category.objects.update_or_create(category_type=4, name=region)
        languages_list.append(reg)

    video_list = []
    for region in movie_info['genres']:
        reg, _ = Category.objects.update_or_create(category_type=3, name=region)
        video_list.append(reg)
    title = ",".join(movie_info['other_names'])
    try:
        release_date = datetime.strptime(movie_info['pubdates'][0].split('(')[0], '%Y-%m-%d')
    except:
        release_date = datetime.now()
    data = {
        'name': movie_info['name'],
        'title': title if title else movie_info['name'],
        'running': True,
        'times': re.search('\d+', movie_info['durations'][0]).group() if len(movie_info['durations']) > 0 else 0,
        'rate': movie_info['rating']['average'] if movie_info['rating']['average'] else 5,
        'release_date': release_date,
        'introduction': movie_info['summary'],
    }

    film, created = FilmInfo.objects.update_or_create(douban=movie_info['id'], defaults=data)
    if not film.poster:
        film.poster.save('', spider.get_image_content(movie_info['image_url']))
        film.save(update_fields=['poster'])
    if created:
        film.enable = False
        film.save(update_fields=['enable'])

    if region_list:
        film.region.set(region_list)
    if languages_list:
        film.language.set(languages_list)
    if video_list:
        film.category.set(video_list)
    reg, _ = Category.objects.update_or_create(category_type=1, name='电影')
    film.channel.set([reg])

    pools = ThreadPoolExecutor(10)
    try:
        for person in spider.get_celebrities_info(movie_id):
            pools.submit(spider.actor_task, person, film)
        pools.shutdown()
    except Exception as e:
        logger.error(e)
    film.running = False
    film.save(update_fields=['running'])
    return film


if __name__ == '__main__':
    print(get_film_info('35102469'))
