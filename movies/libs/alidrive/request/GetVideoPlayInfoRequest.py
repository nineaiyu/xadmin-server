"""..."""
from dataclasses import dataclass

from movies.libs.alidrive import DatClass


@dataclass
class GetVideoPlayInfoRequest(DatClass):
    """..."""
    file_id: str
    drive_id: str = None
