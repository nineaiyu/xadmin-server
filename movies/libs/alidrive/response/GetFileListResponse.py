"""..."""
from dataclasses import dataclass, field
from typing import List

from movies.libs.alidrive import DatClass, BaseFile


@dataclass
class GetFileListResponse(DatClass):
    """..."""
    items: List[BaseFile] = field(default_factory=list)
    next_marker: str = ''
    punished_file_count: int = 0
