"""..."""
from dataclasses import dataclass
from typing import List

from movies.libs.alidrive import DatClass
from movies.libs.alidrive.types import VideoTranscodeTemplate


@dataclass
class GetVideoPlayInfoResponse(DatClass):
    """..."""
    template_list: List[VideoTranscodeTemplate] = None
