"""..."""
from dataclasses import dataclass

from movies.libs.alidrive.types import DatClass


@dataclass
class PersonalSpaceInfo(DatClass):
    """..."""
    used_size: int = None
    total_size: int = None
