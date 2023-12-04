# Create your tests here.

import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'server.settings')
django.setup()

from movies.models import Category

x = [
    "传记片",
    "伦理片",
    "体育片",
    "冒险片",
    "剧情片",
    "动作片",
    "动漫片",
    "历史片",
    "古装片",
    "喜剧片",
    "奇幻片",
    "家庭片",
    "恐怖片",
    "悬疑片",
    "惊悚片",
    "战争片",
    "文艺片",
    "歌舞片",
    "武侠片",
    "爱情片",
    "犯罪片",
    "科幻片",
    "西部片",
    "记录片",
    "音乐片",
    "魔幻片",
]

channel_type = ['电影', '电视剧', '综艺', '动漫', '游戏', '纪录片', '演唱会', '儿童']
for channel in channel_type:
    Category.objects.create(category_type=1, name=channel)

region_type = ['中国大陆', '中国香港', '中国台湾', '美国', '日本', '泰国', '韩国', '英国', '法国', '德国', '加拿大',
               '其他']
for region in region_type:
    Category.objects.create(category_type=2, name=region)

video_type = ['传记片', '伦理片', '体育片', '冒险片', '剧情片', '动作片', '动漫片', '历史片', '古装片', '喜剧片',
              '奇幻片', '家庭片', '恐怖片', '悬疑片', '惊悚片', '战争片', '文艺片', '歌舞片', '武侠片', '爱情片',
              '犯罪片', '科幻片', '西部片', '记录片', '音乐片', '魔幻片']

for video in video_type:
    Category.objects.create(category_type=3, name=video)

language_type = ['中文', '英语', '韩语', '俄罗斯语', '日语']

for language in language_type:
    Category.objects.create(category_type=4, name=language)

subtitle_type = ['中英', '中韩', '中日']

for subtitle in subtitle_type:
    Category.objects.create(category_type=5, name=subtitle)
