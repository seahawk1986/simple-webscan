from pathlib import Path
from pydantic import BaseModel, DirectoryPath, NonNegativeInt

class Config(BaseModel):
    scandir: DirectoryPath
    preferred_resolution: NonNegativeInt
    preferred_mode: str
    preferred_source: str


config = Config(
    scandir = Path('/srv/files/Scans/'),
    preferred_resolution = 300,
    preferred_mode = "Gray",
    preferred_source = "ADF",
)