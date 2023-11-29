"""..."""
from dataclasses import dataclass, field
from typing import List

from movies.libs.alidrive.types import DatClass


@dataclass
class BatchRestoreRequest(DatClass):
    """..."""
    drive_id: str = None
    file_id_list: List[str] = field(default_factory=list)
