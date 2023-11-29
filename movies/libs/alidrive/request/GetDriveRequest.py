"""..."""
from dataclasses import dataclass

from movies.libs.alidrive.types import DatClass


@dataclass
class GetDriveRequest(DatClass):
    """..."""
    drive_id: str = None
