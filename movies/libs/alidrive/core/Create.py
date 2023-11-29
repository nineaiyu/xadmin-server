"""创建文件夹, 上传文件等"""
import base64
import hashlib
from typing import List

import math

from common.cache.storage import UploadPartInfoCache
from movies.libs.alidrive.core.BaseAligo import BaseAligo
from movies.libs.alidrive.core.Config import *
from movies.libs.alidrive.request import *
from movies.libs.alidrive.response import *
from movies.libs.alidrive.types import BaseFile, UploadPartInfo, FileInfo
from movies.libs.alidrive.types.Enum import CheckNameMode


class Create(BaseAligo):
    """创建文件: 1.创建文件 2.上传文件 3.下载文件"""
    # 控制内存消耗 100M 左右，但默认单文件上传只支持 1T，如需更大，手动 调节 Create._UPLOAD_CHUNK_SIZE 的值
    _UPLOAD_CHUNK_SIZE: int = None
    __UPLOAD_CHUNK_SIZE: int = 10485760  # 10 MB

    def create_file(self, body: CreateFileRequest) -> CreateFileResponse:
        """
        创建文件, 可用于上传文件
        :param body: [CreateFileRequest]
        :return: [CreateFileResponse]

        :Example:
        >>> from movies.libs.alidrive import Aligo
        >>> ali = Aligo()
        >>> result = ali.create_file(CreateFileRequest(name='test.txt', parent_file_id='root', type='file', size=1024))
        >>> print(result.file_id)
        """
        response = self._post(ADRIVE_V2_FILE_CREATEWITHFOLDERS, body=body)
        return self._result(response, CreateFileResponse, status_code=201)

    def _core_create_folder(self, body: CreateFolderRequest) -> CreateFileResponse:
        """..."""
        return self.create_file(CreateFileRequest(**body.to_dict()))

    def complete_file(self, body: CompleteFileRequest) -> BaseFile:
        """
        完成文件上传 当文件上传完成时调用
        :param body: [CompleteFileRequest]
        :return: [BaseFile]

        :Example:
        >>> from movies.libs.alidrive import Aligo
        >>> ali = Aligo()
        >>> result = ali.complete_file(CompleteFileRequest(file_id='file_id', part_info_list=[UploadPartInfo(part_number=1)]))
        >>> print(result.file_id)
        """
        response = self._post(V2_FILE_COMPLETE, body=body)
        return self._result(response, BaseFile)

    @staticmethod
    def _get_part_info_list(file_size: int) -> List[UploadPartInfo]:
        """根据文件大小, 返回 part_info_list """
        # 以10MB为一块: 10485760
        return [UploadPartInfo(part_number=i) for i in range(1, math.ceil(file_size / Create.__UPLOAD_CHUNK_SIZE) + 1)]

    def _pre_hash(self, file_info: FileInfo, parent_file_id='root', drive_id=None,
                  check_name_mode: CheckNameMode = 'auto_rename') -> CreateFileResponse:
        body = CreateFileRequest(
            drive_id=drive_id,
            part_info_list=self._get_part_info_list(file_info.file_size),
            parent_file_id=parent_file_id,
            name=file_info.file_name,
            type='file',
            check_name_mode=check_name_mode,
            size=file_info.file_size,
            pre_hash=file_info.pre_hash
        )
        response = self._post(ADRIVE_V2_FILE_CREATEWITHFOLDERS, body=body)
        part_info = self._result(response, CreateFileResponse, [201, 409])
        return part_info

    def _get_proof_code(self, file_path: str, file_size: int) -> str:
        """计算proof_code"""
        md5_int = int(hashlib.md5(self._auth.token.access_token.encode()).hexdigest()[:16], 16)
        # file_size = os.path.getsize(file_path)
        offset = md5_int % file_size if file_size else 0
        if file_path.startswith('http'):
            # noinspection PyProtectedMember
            bys = self._session.get(file_path, headers={
                'Range': f'bytes={offset}-{min(8 + offset, file_size) - 1}'
            }).content
        else:
            with open(file_path, 'rb') as file:
                file.seek(offset)
                bys = file.read(min(8, file_size - offset))
        return base64.b64encode(bys).decode()

    def _content_hash(self, file_info: FileInfo, parent_file_id='root', drive_id=None,
                      check_name_mode: CheckNameMode = 'auto_rename') -> CreateFileResponse:

        body = CreateFileRequest(
            drive_id=drive_id,
            part_info_list=self._get_part_info_list(file_info.file_size),
            parent_file_id=parent_file_id,
            name=file_info.file_name,
            type='file',
            check_name_mode=check_name_mode,
            size=file_info.file_size,
            content_hash=file_info.content_hash,
            content_hash_name="sha1",
            proof_code=file_info.proof_code,
            proof_version='v1'
        )
        response = self._post(ADRIVE_V2_FILE_CREATEWITHFOLDERS, body=body)
        # AttributeError: 'Null' object has no attribute 'rapid_upload'
        if response.status_code == 400:
            body.proof_code = file_info.proof_code
            response = self._post(ADRIVE_V2_FILE_CREATEWITHFOLDERS, body=body)
        part_info = self._result(response, CreateFileResponse, 201)
        return part_info

    def get_upload_url(self, body: GetUploadUrlRequest) -> GetUploadUrlResponse:
        """
        获取上传文件的url
        :param body: [GetUploadUrlRequest]
        :return: [GetUploadUrlResponse]
        """
        response = self._post(V2_FILE_GET_UPLOAD_URL, body=body)
        return self._result(response, GetUploadUrlResponse)

    def pre_hash_check(
            self,
            file_info: dict,
            parent_file_id: str = 'root',
            drive_id: str = None,
            check_name_mode: CheckNameMode = "auto_rename"):

        file_info = FileInfo(**file_info)
        if drive_id is None:
            drive_id = self._auth.drive_obj.default_drive_id

        if file_info.file_size > 1024:  # 1kB
            # 1. pre_hash
            part_info = self._pre_hash(file_info=file_info, parent_file_id=parent_file_id, drive_id=drive_id,
                                       check_name_mode=check_name_mode)
            UploadPartInfoCache(file_info.sid).set_storage_cache(part_info)
            return part_info, part_info.code == 'PreHashMatched'
        else:
            return CreateFileResponse, True

    def get_md5_token(self):
        return str(int(hashlib.md5(self._auth.drive_obj.access_token.encode()).hexdigest()[:16], 16))

    def get_upload_extra(self) -> dict:
        return {'part_size': Create.__UPLOAD_CHUNK_SIZE, 'headers': UNI_HEADERS}

    def get_upload_part_url(self, part_info_list: [UploadPartInfo]) -> [dict]:
        return [{'part_number': part_info.part_number, 'upload_url': part_info.upload_url} for part_info in
                part_info_list]

    def reget_upload_part_url(self, part_info: CreateFileResponse):
        part_info = self.get_upload_url(GetUploadUrlRequest(
            drive_id=part_info.drive_id,
            file_id=part_info.file_id,
            upload_id=part_info.upload_id,
            part_info_list=[UploadPartInfo(part_number=i.part_number) for i in part_info.part_info_list]
        ))
        return self.get_upload_part_url(part_info.part_info_list)

    def upload_complete(self, file_info: dict):
        file_info = FileInfo(**file_info)
        up_cache = UploadPartInfoCache(file_info.sid)
        part_info = up_cache.get_storage_cache()
        # complete
        complete = self.complete_file(CompleteFileRequest(
            drive_id=part_info.drive_id,
            file_id=part_info.file_id,
            upload_id=part_info.upload_id,
            part_info_list=part_info.part_info_list
        ))
        if isinstance(complete, BaseFile):
            self._auth.log.info(f'文件上传完成 {file_info.file_name}')
        else:
            self._auth.log.info(f'文件上传失败 {file_info.file_name}')
        up_cache.del_storage_cache()
        return complete, isinstance(complete, BaseFile)

    def content_hash_check(
            self,
            file_info: dict,
            parent_file_id: str = 'root',
            drive_id: str = None,
            check_name_mode: CheckNameMode = "auto_rename"):
        file_info = FileInfo(**file_info)
        if drive_id is None:
            drive_id = self.default_drive_id
        part_info = self._content_hash(file_info=file_info, parent_file_id=parent_file_id, drive_id=drive_id,
                                       check_name_mode=check_name_mode)
        if part_info.rapid_upload:
            self._auth.log.info(f'文件秒传成功 {file_info.file_name}')
        up_cache = UploadPartInfoCache(file_info.sid)
        up_cache.set_storage_cache(part_info, timeout=3600)
        return part_info, part_info.rapid_upload
