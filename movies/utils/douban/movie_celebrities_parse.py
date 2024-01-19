#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : actor_celebrities_parse.py
# author : ly_13
# date : 1/18/2024

import requests
from bs4 import BeautifulSoup


class MovieCelebritiesParse:
    def __init__(self, movie_id, celebrities_html):
        """
        初始化
        :param movie_id:
        :param celebrities_html:
        """
        self.movie_id = movie_id
        self.celebrities_html = celebrities_html
        self.film_soup = BeautifulSoup(self.celebrities_html, 'lxml')

    def parse(self):
        results = []
        for x in self.film_soup.find_all('div', class_='info'):
            print(111,x.find('span', class_='role'))
            role = x.find('span', class_='role')
            if not role:
                continue
            role = role.contents[0]
            if role.startswith('演员 Actor') or role.startswith('导演 Director') or role.startswith('编剧 Writer'):
                results.append({
                    'id': x.find('a', class_='name')['href'].split('celebrity')[-1].replace('/', ''),
                    'name': x.find('a', class_='name').contents[0],
                    'role': role
                })

        return results


if __name__ == '__main__':
    headers = {
        'User-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/1'
    }
    movie_id = 3642835
    actor_url = f'https://movie.douban.com/subject/{movie_id}/celebrities'

    req = requests.get(actor_url, headers=headers)
    actor_page_parse = MovieCelebritiesParse(movie_id, req.text)
    results = actor_page_parse.parse()
    print(results)
