"""Create class"""

from movies.libs.alidrive.core import *
from movies.libs.alidrive.request import *
from movies.libs.alidrive.response import *
from movies.libs.alidrive.types.Enum import CheckNameMode


class Create(Core):
    """创建上传文件相关"""

    def create_folder(self,
                      name: str,
                      parent_file_id: str = 'root',
                      drive_id: str = None,
                      check_name_mode: CheckNameMode = 'auto_rename') -> CreateFileResponse:
        """
        创建文件夹
        :param name: [str] 文件夹名
        :param parent_file_id: Optional[str] 父文件夹id, 默认为 'root'
        :param drive_id: Optional[str] 指定网盘id, 默认为 None
        :param check_name_mode: Optional[CheckNameMode] 检查文件名模式, 默认为 'auto_rename'
        :return: [CreateFileResponse]

        用法示例:
        >>> from movies.libs.alidrive import Aligo
        >>> ali = Aligo()
        >>> result = ali.create_folder(name='test')
        >>> print(result)
        """
        body = CreateFolderRequest(
            name=name,
            parent_file_id=parent_file_id,
            drive_id=drive_id,
            check_name_mode=check_name_mode,
        )
        return self._core_create_folder(body)
