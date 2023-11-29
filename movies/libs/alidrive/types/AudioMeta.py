"""..."""
# 导包基本原则
# 1. 包内相对导入: from .Type import DatClass
# 2. 包外包导入: from movies.libs.aligo.dataobj import xxx
from dataclasses import dataclass

from .Type import DatClass


@dataclass
class AudioMeta(DatClass):
    """..."""
    bitrate: int = None
    duration: int = None
    sample_rate: int = None
    channels: int = None
