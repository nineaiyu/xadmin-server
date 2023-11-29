"""..."""
from dataclasses import dataclass
from typing import Dict, Union

from movies.libs.alidrive.types import DatClass, DataType


@dataclass
class BatchSubRequest(DatClass):
    """..."""
    body: Union[DataType, Dict]
    id: str
    url: str
    headers: Dict = None
    method: str = 'POST'

    def __post_init__(self):
        self.headers = {"Content-Type": "application/json"}
        super().__post_init__()
