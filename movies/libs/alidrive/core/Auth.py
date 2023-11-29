"""认证模块"""
import base64
import json
import logging
import time
import uuid
from typing import Dict, Optional

import requests

from common.base.magic import MagicCacheData
from movies.libs.alidrive.core.Config import *
from movies.libs.alidrive.types import Token
from movies.models import AliyunDrive


def get_token_from_db(drive_obj):
    fields = ['user_name', 'nick_name', 'user_id', 'default_drive_id', 'default_sbox_drive_id', 'access_token',
              'refresh_token', 'avatar', 'expire_time', 'x_device_id']
    for f in fields:
        setattr(Token, f, getattr(drive_obj, f))
    return Token


def save_token_to_db(drive_obj, token):
    fields = ['user_name', 'nick_name', 'user_id', 'default_drive_id', 'default_sbox_drive_id', 'access_token',
              'refresh_token', 'avatar', 'expire_time', 'x_device_id']
    defaults = {'owner': drive_obj.owner}
    for f in fields:
        defaults[f] = getattr(token, f)
        # setattr(drive_obj, f, getattr(token, f))
    drive_obj, _ = AliyunDrive.objects.filter(owner=drive_obj.owner).update_or_create(defaults=defaults,
                                                                                            user_id=token.user_id)
    MagicCacheData.invalid_cache(f'get_aliyun_drive_{token.user_id}')
    return drive_obj


class Auth(object):
    """..."""

    _SLEEP_TIME_SEC = None
    _SHARE_PWD_DICT = {}

    # x-headers
    _X_PUBLIC_KEY = ('04d9d2319e0480c840efeeb75751b86d0db0c5b9e72c6260a1d846958adceaf9d'
                     'ee789cab7472741d23aafc1a9c591f72e7ee77578656e6c8588098dea1488ac2a')
    _X_SIGNATURE = ('f4b7bed5d8524a04051bd2da876dd79afe922b8205226d65855d02b267422adb1'
                    'e0d8a816b021eaf5c36d101892180f79df655c5712b348c2a540ca136e6b22001')

    def debug_log(self, response: requests.Response):
        """打印错误日志, 便于分析调试"""
        r = response.request
        self.log.warning(f'[method status_code] {r.method} {response.status_code}')
        self.log.warning(f'[url] {response.url}')
        self.log.warning(f'[response body] {response.text[:200]}')

    def __init__(
            self, drive_obj: AliyunDrive,
            refresh_token: str = None,
            level: int = logging.DEBUG,
            proxies: Dict = None,
            request_failed_delay: float = 3,
            requests_timeout: float = 60,
    ):
        """登录验证
        :param drive_obj: 阿里云盘授权对象
        :param refresh_token:
        :param level: (可选) 控制控制台输出
        :param proxies: (可选) 自定义代理 [proxies={"https":"localhost:10809"}],支持 http 和 socks5（具体参考requests库的用法）
        """
        self.drive_obj = drive_obj
        self.log = logging.getLogger(f'{__name__}:{drive_obj}')
        self._requests_timeout = requests_timeout
        self._request_failed_delay = request_failed_delay

        self.log.info(f'日志等级 {logging.getLevelName(level)}')

        #
        self.session = requests.session()
        self.session.trust_env = False
        self.session.proxies = proxies
        self.session.headers.update(UNI_HEADERS)

        self._x_device_id = drive_obj.x_device_id

        #
        self.token: Optional[Token] = Token()

        if refresh_token:
            self.log.debug('登录方式 refresh_token')
            self.token.user_id = self.drive_obj.user_id
            self._refresh_token(refresh_token)
            return

        self.token = get_token_from_db(self.drive_obj)
        self.session.headers.update({
            'Authorization': self.token.access_token,
        })
        self._init_x_headers()
        self.log.debug('登录方式 database access_token')
        self.session.headers.update({
            'Authorization': self.token.access_token
        })

    def _create_session(self):
        self.post(USERS_V1_USERS_DEVICE_CREATE_SESSION, body={
            'deviceName': f'movies - {self.drive_obj.nick_name}',
            'modelName': "Windows 操作系统",
            'pubKey': self._X_PUBLIC_KEY,
        })

    def _renew_session(self):
        self.post(USERS_V1_USERS_DEVICE_RENEW_SESSION, body={})

    def _init_x_headers(self):
        if not self._x_device_id:
            # 如果 self._x_device_id 为 None，尝试从 token 中获取（来自文件）
            self._x_device_id = self.token.x_device_id
        if not self._x_device_id:
            # 如果文件中未存储，则说明还没有，则生成
            self._x_device_id = uuid.uuid4().hex
        # 设置 x-headers
        self.session.headers.update({
            'x-device-id': self._x_device_id,
            'x-signature': self._X_SIGNATURE
        })
        # 将 x-headers 放到 token 对象中，用以保存
        self.token.x_device_id = self._x_device_id

    def _save(self):
        self.drive_obj = save_token_to_db(self.drive_obj, self.token)

    def get_login_qr(self) -> dict:
        self.session.get(AUTH_HOST + V2_OAUTH_AUTHORIZE, params={
            'login_type': 'custom',
            'response_type': 'code',
            'redirect_uri': 'https://www.aliyundrive.com/sign/callback',
            'client_id': CLIENT_ID,
            'state': r'{"origin":"file://"}',
        }, stream=True).close()

        #
        session_id = self.session.cookies.get('SESSIONID')
        self.log.debug(f'session {session_id}')
        response = self.session.get(
            PASSPORT_HOST + NEWLOGIN_QRCODE_GENERATE_DO, params=UNI_PARAMS
        )
        self._log_response(response)
        data = response.json()['content']['data']
        qr_link = data['codeContent']
        self.log.info(f'登录二维码 {qr_link}')
        return data

    def check_scan_qr(self, data, *args, **kwargs) -> dict:
        response = self.session.post(
            PASSPORT_HOST + NEWLOGIN_QRCODE_QUERY_DO,
            data=data, params=UNI_PARAMS
        )
        self._log_response(response)
        login_data = response.json()['content']['data']
        # noinspection PyPep8Naming
        qrCodeStatus = login_data['qrCodeStatus']
        # noinspection SpellCheckingInspection
        if qrCodeStatus == 'NEW':
            return {'code': 5, 'msg': '等待扫描'}
        elif qrCodeStatus == 'SCANED':
            self.log.info('已扫描 等待确认')
            return {'code': 3, 'msg': '已扫描 等待确认'}
        elif qrCodeStatus == 'CONFIRMED':
            self.log.info(f'已确认 可关闭二维码窗口')
            if response.status_code != 200:
                self.log.error('登录失败')
                return {'code': 2, 'msg': '登录失败'}

            biz_ext = response.json()['content']['data']['bizExt']
            biz_ext = base64.b64decode(biz_ext).decode('gb18030')

            # 获取解析出来的 refreshToken, 使用这个token获取下载链接是直链, 不需要带 referer header
            refresh_token = json.loads(biz_ext)['pds_login_result']['refreshToken']
            if self._refresh_token(refresh_token):
                return {'code': 0, 'msg': ''}
            else:
                return {'code': 4, 'msg': 'token获取失败'}
        else:
            self.log.warning('未知错误 可能二维码已经过期')
            return {'code': 1, 'msg': '未知错误 可能二维码已经过期'}

    @MagicCacheData.make_cache(timeout=3, key_func=lambda *args: args[0].token.user_id)
    def _refresh_token(self, refresh_token=None):
        """刷新 token"""
        if refresh_token is None:
            refresh_token = self.drive_obj.refresh_token
        self.log.info(f'刷新 token refresh_token {refresh_token}')
        response = self.session.post(
            API_HOST + V2_ACCOUNT_TOKEN,
            json={
                'refresh_token': refresh_token,
                'grant_type': 'refresh_token'
            }
        )
        self._log_response(response)
        if response.status_code == 200:
            self.log.info('刷新 token 成功')
            # noinspection PyProtectedMember
            self.token = Token(**response.json())
            self._init_x_headers()
            self._save()
        else:
            self.log.warning(f'刷新 token 失败, {response}')
            self.drive_obj.active = False
            self.drive_obj.save(update_fields=['active', 'updated_time'])
            MagicCacheData.invalid_cache(f'get_aliyun_drive_{self.drive_obj.user_id}')
            return False

        self.session.headers.update({
            'Authorization': self.token.access_token
        })
        return self.token

    _VERIFY_SSL = True

    def request(self, method: str, url: str, params: Dict = None,
                headers: Dict = None, data=None, body: Dict = None) -> requests.Response:
        """统一请求方法"""
        # 删除值为None的键
        if body is not None:
            body = {k: v for k, v in body.items() if v is not None}

        if data is not None and isinstance(data, dict):
            data = {k: v for k, v in data.items() if v is not None}

        response = None
        for i in range(1, 6):
            try:
                response = self.session.request(
                    method=method, url=url, params=params, data=data,
                    headers=headers, verify=self._VERIFY_SSL, json=body, timeout=self._requests_timeout
                )
            except requests.exceptions.ConnectionError as e:
                self.log.warning(e)
                time.sleep(self._request_failed_delay)
                continue

            status_code = response.status_code
            self._log_response(response)

            if status_code == 401:
                if b'"ShareLinkTokenInvalid"' in response.content:
                    # 刷新 share_token
                    share_id = body['share_id']
                    share_pwd = body['share_pwd']
                    r = self.post(
                        V2_SHARE_LINK_GET_SHARE_TOKEN,
                        body={'share_id': share_id, 'share_pwd': share_pwd}
                    )
                    share_token = r.json()['share_token']
                    headers['x-share-token'].share_token = share_token
                elif b'"UserDeviceOffline"' in response.content:
                    self._create_session()
                else:
                    self._refresh_token()
                continue

            if status_code in [429, 502, 504]:
                if self._SLEEP_TIME_SEC is None:
                    sleep_int = 5 ** (i % 4)
                else:
                    sleep_int = self._SLEEP_TIME_SEC
                err_msg = None
                if status_code == 429:
                    err_msg = '请求太频繁'
                elif status_code == 502:
                    err_msg = '内部网关错误'
                elif status_code == 504:
                    err_msg = '内部网关超时'
                self.log.warning(f'{err_msg}，暂停 {sleep_int} 秒钟')
                time.sleep(sleep_int)
                continue

            if status_code == 500:
                raise Exception(response.content)

            if status_code == 400:
                if b'"DeviceSessionSignatureInvalid"' in response.content \
                        or b'"not found device info"' in response.content:
                    self._create_session()
                    continue
                elif b'"InvalidResource.FileTypeFolder"' in response.content:
                    self.log.warning(
                        '请区分 文件 和 文件夹，有些操作是它们独有的，比如获取下载链接，很显然 文件夹 是没有的！')

            if status_code == 403:
                if b'"SharelinkCreateExceedDailyLimit"' in response.content:
                    raise Exception(response.content)

            return response

        self.log.info(f'重试 5 次仍旧失败')

    def get(self, path: str, host: str = API_HOST, params: dict = None, headers: dict = None) -> requests.Response:
        """..."""
        return self.request(method='GET', url=host + path, params=params, headers=headers)

    def post(self, path: str, host: str = API_HOST, params: dict = None, headers: dict = None,
             data: dict = None, body: dict = None, ignore_auth: bool = False) -> requests.Response:
        """..."""
        if ignore_auth:
            if headers is None:
                headers = {}
            headers['Authorization'] = None
        return self.request(method='POST', url=host + path, params=params,
                            data=data, headers=headers, body=body)

    def _log_response(self, response: requests.Response):
        """打印响应日志"""
        self.log.info(
            f'{response.request.method} {response.url} {response.status_code} {len(response.content)}'
        )

    def device_logout(self):
        return self.post(USERS_V1_USERS_DEVICE_LOGOUT)
