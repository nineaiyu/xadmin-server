#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : movie_person_page_parse.py
# author : ly_13
# date : 1/18/2024

import re
import requests
from bs4 import BeautifulSoup


class PersonPageParse:
    def __init__(self, person_id, person_info_html):
        """
        初始化
        :param person_id:
        :param person_info_html:
        """
        self.person_id = person_id
        self.person_info_html = person_info_html
        self.person_soup = BeautifulSoup(self.person_info_html, 'lxml')

    def _get_person_name(self):
        """
        获取演员姓名
        :return:
        """
        try:
            content_str = str(self.person_soup.find('div', id='content'))
            # print(content_str)
            name_str = re.search(r'<h1>.*</h1>', content_str).group()
            name = name_str.replace('<h1>', '').replace('</h1>', '')
        except Exception as err:
            name = ''
        return name

    def _get_person_image_url(self, name):
        """
        获取演员图片
        :return:
        """
        try:
            image_url = str(self.person_soup.find('img', title=name.split(' ')[0])['src'])
        except Exception as err:
            image_url = ''
        return image_url

    def _get_person_gender(self):
        """
        获取演员性别
        :return:
        """
        try:
            gender = ''
            person_info = BeautifulSoup(str(self.person_soup.find('div', {'class': 'info'})), 'lxml')
            person_info = person_info.find_all('li')
            for line in person_info:
                line = str(line).replace(' ', '').replace('\n', '')
                if '性别' in line:
                    gender_str = str(re.search(r'性别</span>:.*</li>', line).group())
                    gender = gender_str.replace('性别</span>:', '').replace('</li>', '')
                    break
        except Exception as err:
            gender = ''
        return gender

    def _get_person_birthday(self):
        """
        获取演员出生日期
        :return:
        """
        try:
            birthday = ''
            person_info = BeautifulSoup(str(self.person_soup.find('div', {'class': 'info'})), 'lxml')
            person_info = person_info.find_all('li')
            for line in person_info:
                line = str(line).replace(' ', '').replace('\n', '')
                if '出生日期' in line:
                    birtyday_str = str(re.search(r'出生日期</span>:.*</li>', line).group())
                    birthday = birtyday_str.replace('出生日期</span>:', '').replace('</li>', '')
                    break
        except Exception as err:
            birthday = ''
        return birthday

    def _get_person_birthplace(self):
        """
        获取演员出生地
        :return:
        """
        try:
            birthplace = ''
            person_info = BeautifulSoup(str(self.person_soup.find('div', {'class': 'info'})), 'lxml')
            person_info = person_info.find_all('li')
            for line in person_info:
                line = str(line).replace(' ', '').replace('\n', '')
                if '出生地' in line:
                    birthplace_str = str(re.search(r'出生地</span>:.*</li>', line).group())
                    birthplace = birthplace_str.replace('出生地</span>:', '').replace('</li>', '')
        except Exception as err:
            birthplace = ''
        return birthplace

    def _get_person_profession(self):
        """
        获取演员职业
        :return:
        """
        try:
            profession = ''
            person_info = BeautifulSoup(str(self.person_soup.find('div', {'class': 'info'})), 'lxml')
            person_info = person_info.find_all('li')
            for line in person_info:
                line = str(line).replace(' ', '').replace('\n', '')
                if '职业' in line:
                    profession_str = str(re.search(r'职业</span>:.*</li>', line).group())
                    profession = profession_str.replace('职业</span>:', '').replace('</li>', '')
        except Exception as err:
            profession = ''
        return profession

    def _get_person_other_chinese_name(self):
        """
        获取演员其他中文名称
        :return:
        """
        try:
            other_chinese_name = ''
            person_info = BeautifulSoup(str(self.person_soup.find('div', {'class': 'info'})), 'lxml')
            person_info = person_info.find_all('li')
            for line in person_info:
                line = str(line).replace(' ', '').replace('\n', '')
                if '更多中文名' in line:
                    other_chinese_name_str = str(re.search(r'更多中文名</span>:.*</li>', line).group())
                    other_chinese_name = other_chinese_name_str.replace('更多中文名</span>:', '').replace('</li>', '')
        except Exception as err:
            other_chinese_name = ''
        return other_chinese_name

    def _get_person_other_english_name(self):
        """
        获取演员其他英文名称
        :return:
        """
        try:
            other_english_name = ''
            person_info = BeautifulSoup(str(self.person_soup.find('div', {'class': 'info'})), 'lxml')
            person_info = person_info.find_all('li')
            for line in person_info:
                line = str(line).replace(' ', '').replace('\n', '')
                if '更多外文名' in line:
                    other_english_name_str = str(re.search(r'更多外文名</span>:.*</li>', line).group())
                    other_english_name = other_english_name_str.replace('更多外文名</span>:', '').replace('</li>', '')
        except Exception as err:
            other_english_name = ''
        return other_english_name

    def _get_person_constellation(self):
        """
        获取演员星座
        :return:
        """
        try:
            constellation = ''
            person_info = BeautifulSoup(str(self.person_soup.find('div', {'class': 'info'})), 'lxml')
            person_info = person_info.find_all('li')
            for line in person_info:
                line = str(line).replace(' ', '').replace('\n', '')
                if '星座' in line:
                    oconstellation_str = str(re.search(r'星座</span>:.*</li>', line).group())
                    constellation = oconstellation_str.replace('星座</span>', '').replace('</li>', '')
        except Exception as err:
            constellation = ''
        return constellation

    def _get_person_family_member(self):
        """
        获取演员家庭成员
        :return:
        """
        try:
            family_member = ''
            person_info = BeautifulSoup(str(self.person_soup.find('div', {'class': 'info'})), 'lxml')
            person_info = person_info.find_all('li')
            for line in person_info:
                line = str(line).replace(' ', '').replace('\n', '')
                if '家庭成员' in line:
                    family_member_str = str(re.search(r'家庭成员</span>:.*</li>', line).group())
                    family_member = family_member_str.replace('家庭成员</span>', '').replace('</li>', '')
        except Exception as err:
            family_member = ''
        return family_member

    def _get_person_introduction(self):
        """
        获取演员介绍
        :return:
        """
        try:
            introduction = ''
            try:
                # all content
                introduction = str(self.person_soup.find('span', class_='all hidden').text)
                introduction = introduction.replace('\n', '').replace('\u3000', '').replace(' ', '')
            except:
                # short content
                introduction = str(self.person_soup.find_all('div', id='intro')).replace('\n', '')
                introduction = str(re.search('<div class="bd">.*</div>', introduction).group())
                introduction = introduction.replace('<div class="bd">', '').replace('</div>', '').replace('\u3000',
                                                                                                          '').replace(
                    ' ', '')
        except Exception as err:
            introduction = ''
        return introduction

    def parse(self):
        """
        获取演员信息
        :return:
        """
        name = self._get_person_name()  # 演员姓名
        image_url = self._get_person_image_url(name)  # 演员图片链接
        constellation = self._get_person_constellation()  # 演员星座
        gender = self._get_person_gender()  # 演员性别
        birthday = self._get_person_birthday()  # 演员出生日期
        birthplace = self._get_person_birthplace()  # 演员出生地
        profession = self._get_person_profession()  # 演员职业
        other_chinese_name = self._get_person_other_chinese_name()  # 演员其他中文名称
        other_english_name = self._get_person_other_english_name()  # 演员其他英文名称
        family_member = self._get_person_family_member()  # 演员家庭成员
        introduction = self._get_person_introduction()  # 获取演员介绍

        person_info_json = {
            'id': self.person_id.split('celebrity')[-1].replace('/',''),
            'name': name,
            'image_url': image_url,
            'constellation': constellation,
            'gender': gender,
            'birthday': birthday,
            'birthplace': birthplace,
            'profession': profession,
            'other_chinese_name': other_chinese_name,
            'other_english_name': other_english_name if other_english_name else name.replace(name.split(' ')[0],'').strip(),
            'family_member': family_member,
            'introduction': introduction
        }
        return person_info_json


if __name__ == '__main__':
    headers = {
        'User-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/1'
    }

    person_id = '/celebrity/1338451/'
    person_url = 'https://movie.douban.com' + str(person_id)
    person_info_html = requests.get(person_url, headers=headers).text
    print(person_info_html)
    person_page_parse = PersonPageParse(person_id, person_info_html)
    print(person_page_parse.parse())