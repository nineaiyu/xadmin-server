"""..."""
from dataclasses import dataclass

from movies.libs.alidrive.types import DatClass


@dataclass
class GetDefaultDriveRequest(DatClass):
    """..."""
    user_id: str
