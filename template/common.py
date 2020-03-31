# -*- coding: UTF-8 -*-
import requests
from bs4 import BeautifulSoup
import re
from time import sleep
import os
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
import threading

from utils import (
    keep_link,
    str_url_encode,
    is_single_media,
    get_full_link,
)

from displaypolicy import (
    default_policy,
    default_id_policy,
)


class NewsExtractor(object):
    _listURLs = []
    _lang = ""
    _sendList = []
    _headers = {}
    _proxies = {}
    _display_policy = default_policy
    _id_policy = default_id_policy

    # TODO: compatibility
    _list_selector = '.dataList > .clearfix > h3 > a, ' \
                     '.newsList2 > h2 > a, ' \
                     '.newsList > h2 > a'

    _time_selector = '.h-info > span:nth-child(1), ' \
                     '.time'

    _title_selector = '.h-title, ' \
                      '#conTit > h1, ' \
                      '.title, ' \
                      '.Btitle'

    _source_selector = '.h-info > span:nth-child(2), ' \
                       '.source'

    _paragraph_selector = 'p'

    def __init__(self, listURLs, sendList=[], lang='', display_policy=default_policy, headers=None, proxies={}):
        self._listURLs = listURLs
        self._lang = lang
        self._sendList = sendList
        self._display_policy = display_policy

        if headers:
            self._headers = headers
        else:
            self._headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                              'Chrome/80 Safari/537.36'}
        self._proxies = proxies

        self.TOKEN = os.getenv("TOKEN")
        self.DATABASE_URL = os.getenv("DATABASE_URL")
        engine = create_engine(self.DATABASE_URL)
        self.db = scoped_session(sessionmaker(bind=engine))

    def set_list_selector(self, list_selector):
        self._list_selector = list_selector

    def set_time_selector(self, time_selector):
        self._time_selector = time_selector

    def set_title_selector(self, title_selector):
        self._title_selector = title_selector

    def set_source_selector(self, source_selector):
        self._source_selector = source_selector

    def set_paragraph_selector(self, paragraph_selector):
        self._paragraph_selector = paragraph_selector

    def set_id_policy(self, id_policy):
        self._id_policy = id_policy

    def get_list(self, listURL):
        res = requests.get(listURL, headers=self._headers)
        # print(res.text)
        if res.status_code == 200:
            res.encoding = 'utf-8'
            # print(res.text)

            news_list = []

            soup = BeautifulSoup(res.text, 'lxml')
            data = soup.select(self._list_selector)
            # print(data)

            for item in data:
                link = get_full_link(item.get('href'), listURL)

                result = {
                    "title": item.get_text(),
                    "link": link,
                    'ID': self._id_policy(link)
                }
                news_list.append(result)

            return news_list
        else:
            print('List URL error exception! ' + str(res.status_code))
            if res.status_code == 403:
                print('May be your header did not work.')
            return []

    def get_full(self, url, item):
        res = requests.get(url, headers=self._headers)
        res.encoding = 'utf-8'
        # print(res.text)
        time = ''
        source = ''
        title = ''

        soup = BeautifulSoup(res.text, 'lxml')

        # Get release time and source
        time_select = soup.select(self._time_selector)
        try:
            for text in time_select:
                time = text.getText().strip()
                time = time.split('丨')[0]
                if time:
                    break
            time = time.split('\n')[0]
            time = time.split('	')[0]
            #print(time)

            # If time is too long, maybe get irrelevant  info
            if len(time) > 100:
                time = ''
        except IndexError:  # Do not have this element because of missing/403/others
            time = ""

        source_select = soup.select(self._source_selector)
        try:
            source = source_select[0].getText().strip().replace('\n', '')
        except IndexError:  # Do not have this element because of missing/403/others
            source = ""

        # Get news title
        title_select = soup.select(self._title_selector)
        try:
            title = title_select[0].getText().strip()
        except IndexError:  # Do not have this element because of missing/403/others
            title = item['title']

        # Get news body
        # Two select ways:
        # Mobile news page: '.main-article > p'
        # Insatnce news page: '#p-detail > p'
        paragraph_select = soup.select(self._paragraph_selector)
        # return paragraph_select
        # print(paragraph_select)

        paragraphs = ""
        blank_flag = False
        for p in paragraph_select:
            link_str = keep_link(str(p), url).strip('\u3000').strip('\n').strip()

            # If there is only ONE [Media] link, it should be concerned as a word.
            # This is the
            if link_str != "" and not is_single_media(link_str):
                if blank_flag:
                    link_str = '\n\n' + link_str
                    blank_flag = False
                paragraphs += link_str + '\n\n'
            elif link_str != "":
                paragraphs += link_str + ' '
                blank_flag = True
        # print(paragraphs)

        return {'title': title, 'time': time, 'source': source, 'paragraphs': paragraphs, 'link': url}

    def post(self, item, news_id):

        # Get display policy by item info
        po, parse_mode, disable_web_page_preview = self._display_policy(item)

        # Must url encode the text
        po = str_url_encode(po)

        res = None
        for chat_id in self._sendList:
            # https://core.telegram.org/bots/api#sendmessage
            post_url = 'https://api.telegram.org/bot' + self.TOKEN + '/sendMessage?chat_id=' + chat_id + '&text=' + po + '&parse_mode=' + parse_mode + '&disable_web_page_preview=' + disable_web_page_preview
            res = requests.get(post_url, proxies=self._proxies)
            if res.status_code == 200:
                self.db.execute("INSERT INTO news (news_id, time) VALUES (:news_id, NOW())",
                                {"news_id": news_id})
                # Commit changes to database
                self.db.commit()
            else:
                print('REEOR! NOT POSTED BECAUSE OF ' + str(res.status_code))
                print(res.text)
        return res

    def is_posted(self, news_id):
        rows = self.db.execute("SELECT * FROM news WHERE news_id = :news_id",
                               {"news_id": news_id})
        if rows.rowcount == 0:
            return False
        else:
            return True

    def action(self):
        nlist = []
        for link in self._listURLs:
            nlist += self.get_list(link)

        nlist.reverse()
        # print(nlist)

        total = 0
        posted = 0
        for item in nlist:
            if not self.is_posted(item['ID']):
                message = self.get_full(item['link'], item=item)
                # print(message)

                # Post the message by api
                res = self.post(message, item['ID'])
                print(str(item['ID']) + " " + str(res.status_code))
                total += 1
            else:
                posted += 1
                # print(item['ID'] + 'Posted!')
        return total, posted

    def poll(self, time=30):
        def work():
            while (True):
                total, posted = self.action()
                if total + posted == 0:
                    print('Empty list:')
                print(self._lang + ': ' + str(total) + ' succeeded,' + str(posted) + ' posted.', end=' ')
                print('Wait ' + str(time) + 's to restart!')
                sleep(time)

        t = threading.Thread(target=work)
        t.start()


class NewsExtractorJSON(NewsExtractor):

    def __init__(self, listURLs, sendList, lang='', display_policy=default_policy):
        super(NewsExtractorJSON, self).__init__(listURLs, sendList=sendList, lang=lang, display_policy=display_policy)

    def get_list(self, listURL):
        res = requests.get(listURL, headers=self._headers)
        if res.status_code == 200:
            res.encoding = 'utf-8'
            # print(res.text)

            newsList = []
            list_json = None
            try:
                list_json = json.loads(res.text)
            except json.decoder.JSONDecodeError:
                try:
                    list_json = json.loads(res.text[1:-2])  # Remove brackets and load as json
                except Exception:
                    pass

            for item in list_json['data']['list']:
                i = {'ID': item['DocID'],
                     'link': item['LinkUrl'],
                     'title': item['Title'],
                     "PubTime": item["PubTime"],
                     "SourceName": item["SourceName"],
                     "Author": item["Author"]}
                newsList.append(i)

            return newsList
        else:
            print('List URL error exception!')
            return None

    def get_full(self, url, item=None):
        res = requests.get(url, headers=self._headers)
        res.encoding = 'utf-8'
        # print(res.text)
        time = ''
        source = ''
        title = ''

        soup = BeautifulSoup(res.text, 'lxml')

        time = item["PubTime"]
        source = item["SourceName"]
        title = item['title']

        # Get news body
        # Two select ways:
        # Mobile news page: '.main-article > p'
        # Insatnce news page: '#p-detail > p'
        paragraph_select = soup.select('p')
        # return paragraph_select
        # print(paragraph_select)

        paragraphs = ""
        for p in paragraph_select:
            link_str = keep_link(str(p), url).strip('\u3000').strip('\n').strip()
            if link_str != "":
                paragraphs += link_str + '\n\n'
        # print(paragraphs)

        return {'title': title, 'time': time, 'source': source, 'paragraphs': paragraphs, 'link': url}


print("DELETED!!")