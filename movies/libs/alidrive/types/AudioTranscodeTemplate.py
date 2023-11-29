"""..."""
from dataclasses import dataclass

from .Enum import MediaTranscodeStatus
from .MediaTransCodeTemplate import MediaTransCodeTemplate


@dataclass
class AudioTranscodeTemplate(MediaTransCodeTemplate):
    """..."""
    template_id: str = None
    status: MediaTranscodeStatus = None
    url: str = None
