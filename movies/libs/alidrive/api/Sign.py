"""Create class"""

from dataclasses import dataclass, field
from typing import List, Dict

from datclass import DatClass

from movies.libs.alidrive.core import *
from movies.libs.alidrive.core import Config


@dataclass
class Reward(DatClass):
    action: str = None
    background: str = None
    bottleId: str = None
    bottleName: str = None
    bottleShareId: str = None
    color: str = None
    description: str = None
    detailAction: str = None
    goodsId: int = None
    name: str = None
    notice: str = None
    subNotice: str = None


@dataclass
class Signinlogs(DatClass):
    calendarChinese: str = None
    calendarDay: str = None
    calendarMonth: str = None
    day: int = None
    icon: str = None
    isReward: bool = None
    notice: str = None
    pcAndWebIcon: str = None
    poster: str = None
    reward: Reward = None
    rewardAmount: int = None
    status: str = None
    themes: str = None
    type: str = None


@dataclass
class Result(DatClass):
    blessing: str = None
    description: str = None
    isReward: bool = None
    pcAndWebRewardCover: str = None
    rewardCover: str = None
    signInCount: int = None
    signInCover: str = None
    signInLogs: List[Signinlogs] = field(default_factory=list)
    signInRemindCover: str = None
    subject: str = None
    title: str = None


@dataclass
class SignInList(DatClass):
    arguments: str = None
    code: str = None
    maxResults: str = None
    message: str = None
    nextToken: str = None
    result: Result = None
    success: bool = None
    totalCount: str = None


@dataclass
class SignInReward(DatClass):
    arguments: str = None
    code: str = None
    maxResults: str = None
    message: str = None
    nextToken: str = None
    result: Reward = None
    success: bool = None
    totalCount: str = None


class Sign(Core):
    V1_ACTIVITY_SIGN_IN_LIST = '/v1/activity/sign_in_list'
    V1_ACTIVITY_SIGN_IN_REWARD = '/v1/activity/sign_in_reward'

    def _sign_in(self, body: Dict = None):
        return self._post(
            self.V1_ACTIVITY_SIGN_IN_LIST,
            host=Config.MEMBER_HOST,
            body=body, params={'_rx-s': 'mobile'}
        )

    def sign_in_list(self) -> SignInList:
        resp = self._sign_in({'isReward': True})
        return SignInList.from_str(resp.text)

    def sign_in_festival(self):
        return self._sign_in()

    def sign_in_reward(self, day) -> SignInReward:
        resp = self._post(
            self.V1_ACTIVITY_SIGN_IN_REWARD,
            host=Config.MEMBER_HOST,
            body={'signInDay': day},
            params={'_rx-s': 'mobile'}
        )
        return SignInReward.from_str(resp.text)

