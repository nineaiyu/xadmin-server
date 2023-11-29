"""..."""
from dataclasses import dataclass

from movies.libs.alidrive.types import DatClass


@dataclass
class MoveFileToTrashRequest(DatClass):
    """..."""
    file_id: str
    drive_id: str = None
