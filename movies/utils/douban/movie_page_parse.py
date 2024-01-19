#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : movie_page_parse.py
# author : ly_13
# date : 1/18/2024

import re

import requests
from bs4 import BeautifulSoup


class MoviePageParse:
    def __init__(self, movie_id, movie_info_html):
        """
        初始化
        :param movie_id:
        :param movie_info_html:
        """
        self.movie_id = movie_id
        self.movie_info_html = movie_info_html
        self.film_soup = BeautifulSoup(self.movie_info_html, 'lxml')

    def _get_movie_name(self):
        """
        获取电影姓名
        :param film_soup:
        :return:
        """
        try:
            name = str(self.film_soup.find('span', property='v:itemreviewed').text)
        except Exception as err:
            name = ''

        return name

    def _get_movie_image_url(self):
        """
        获取电影图片链接
        :return:
        """
        try:
            image_url = str(self.film_soup.find('img', title='点击看更多海报')['src'])
        except Exception as err:
            image_url = ''
        return image_url

    def _get_movie_genres(self):
        """
        获取电影类型
        :return:
        """
        try:
            film_info = str(self.film_soup.find('div', {'id': 'info'}))
            genres = []
            genres_text = re.search(r'类型:</span> .*<br/>', film_info).group()
            genres_text = genres_text.replace('类型:</span> ', '').replace('<br/>', '')
            genres_text_list = genres_text.split('</span>')
            while '' in genres_text_list:
                genres_text_list.remove('')
            for genre in genres_text_list:
                genre_name = re.sub(r'<span.*>', '', genre).replace('/', '').replace(' ', '')
                genres.append(genre_name)
        except Exception as err:
            genres = []

        return genres

    def _get_movie_countries(self):
        """
        获取电影制片国家/地区
        :return:
        """
        try:
            film_info = str(self.film_soup.find('div', {'id': 'info'}))
            countries_text = re.search(r'制片国家/地区:</span>.*<br/>', film_info).group()
            countries_text = countries_text.replace('制片国家/地区:</span>', '').replace('<br/>', '')
            countries = [country.replace(' ', '') for country in countries_text.split('/')]
        except Exception as err:
            countries = []

        return countries

    def _get_movie_languages(self):
        """
        获取电影语言
        :return:
        """
        try:
            film_info = str(self.film_soup.find('div', {'id': 'info'}))
            languages_text = re.search(r'语言:</span>.*<br/>', film_info).group()
            languages_text = languages_text.replace('语言:</span>', '').replace('<br/>', '')
            languages = [language.replace(' ', '') for language in languages_text.split('/')]
        except Exception as err:
            languages = []

        return languages

    def _get_movie_pubdates(self):
        """
        获取电影上映时间
        :return:
        """
        try:
            film_info = str(self.film_soup.find('div', {'id': 'info'}))
            pubdates = []
            try:
                release_text = re.search(r'上映日期:</span> .*<br/>', film_info).group()
                release_text = release_text.replace('上映日期:</span> ', '').replace('<br/>', '')
                pubdates_text_list = release_text.split('</span>')
            except:
                premiere_text = re.search(r'首播:</span>.*<br/>', film_info).group()
                premiere_text = premiere_text.replace('首播:</span> ', '').replace('<br/>', '')
                pubdates_text_list = premiere_text.split('</span>')
            while '' in pubdates_text_list:
                pubdates_text_list.remove('')
            for pubdate in pubdates_text_list:
                pubdate_name = re.sub(r'<span.*>', '', pubdate).replace('/', '').replace(' ', '')
                pubdates.append(pubdate_name)
        except Exception as err:
            pubdates = []
        return pubdates

    def _get_movie_episodes(self):
        """
        获取电影集数
        :return:
        """
        try:
            film_info = str(self.film_soup.find('div', {'id': 'info'}))
            episodes_text = re.search(r'集数:</span>.*<br/>', film_info).group()
            episodes_text = episodes_text.replace('集数:</span>', '').replace('<br/>', '').replace(' ', '')
            episodes = episodes_text
        except Exception as err:
            episodes = '1'
        return episodes

    def _get_movie_durations(self):
        """
        获取电影片长
        :return:
        """
        try:
            film_info = str(self.film_soup.find('div', {'id': 'info'}))
            durations = []
            durations_text = re.search(r'片长:</span> .*<br/>', film_info).group()
            durations_text = durations_text.replace('片长:</span> ', '').replace('<br/>', '')
            durations_text_list = durations_text.split('</span>')
            while '' in durations_text_list:
                durations_text_list.remove('')
            for duration in durations_text_list:
                duration_name = re.sub(r'<span.*>', '', duration).replace('/', '').replace(' ', '')
                durations.append(duration_name)
        except Exception as err:
            durations = []

        return durations

    def _get_movie_other_names(self):
        """
        获取电影其他名称
        :return:
        """
        try:
            film_info = str(self.film_soup.find('div', {'id': 'info'}))
            other_names = []
            other_names_text = re.search('又名:</span>.*<br/>', film_info).group()
            other_names_text = other_names_text.replace('又名:</span>', '').replace('<br/>', '')
            other_names_text_list = other_names_text.split('/')
            while '' in other_names_text_list:
                other_names_text_list.remove('')
            for other_name in other_names_text_list:
                name = other_name.replace('/', '').replace(' ', '')
                other_names.append(name)
        except Exception as err:
            other_names = []

        return other_names

    def _get_movie_summary(self):
        """
        获取电影简介
        :return:
        """
        try:
            try:
                # all content
                summary = str(self.film_soup.find('span', class_='all hidden').text)
                summary = summary.replace('\n', '').replace('\u3000', '').replace(' ', '')
            except Exception as err:
                # short content
                summary = str(self.film_soup.find('span', property='v:summary').text)
                summary = summary.replace('\n', '').replace('\u3000', '').replace(' ', '')
        except Exception as err:
            summary = ''
        return summary

    def _get_movie_rating(self):
        """
        获取电影评分
        :return:
        """
        try:
            average = str(self.film_soup.find('strong', property='v:average').text)
            reviews_count = str(self.film_soup.find('span', property='v:votes').text)
            rating = {
                'average': average,
                'reviews_count': reviews_count
            }
        except Exception as err:
            rating = {
                'average': '',
                'reviews_count': ''
            }
        return rating

    def parse(self):
        """
        获取电影信息（包含电视剧、综艺、动漫、纪录片、短片）
        :return:
        """
        name = self._get_movie_name()  # 电影姓名
        image_url = self._get_movie_image_url()  # 电影图片链接
        genres = self._get_movie_genres()  # 电影类型
        countries = self._get_movie_countries()  # 电影制片国家/地区
        languages = self._get_movie_languages()  # 电影语言
        pubdates = self._get_movie_pubdates()  # 电影上映时间
        episodes = self._get_movie_episodes()  # 电影集数
        durations = self._get_movie_durations()  # 电影片长
        other_names = self._get_movie_other_names()  # 电影其他名称
        summary = self._get_movie_summary()  # 电影简介
        rating = self._get_movie_rating()  # 电影评分

        movie_info_json = {
            'id': self.movie_id,
            'image_url': image_url,
            'name': name,
            'genres': genres,
            'countries': countries,
            'languages': languages,
            'pubdates': pubdates,
            'episodes': episodes,
            'durations': durations,
            'other_names': other_names,
            'summary': summary,
            'rating': rating
        }
        return movie_info_json


if __name__ == '__main__':
    headers = {
        'User-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/1'
    }

    movie_id = 26266893
    movie_url = f'https://movie.douban.com/subject/{movie_id}/'

    req = requests.get(movie_url, headers=headers)
    movie_page_parse = MoviePageParse(movie_id, req.text)
    movie_info_json = movie_page_parse.parse()
    print(movie_info_json)
