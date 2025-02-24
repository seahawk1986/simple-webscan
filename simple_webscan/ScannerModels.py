from pydantic import BaseModel, NonNegativeInt, PositiveInt, Field
from enum import IntEnum

class SaneType(IntEnum):
    BOOL = 0
    INT = 1
    FIXED = 2
    STRING = 3
    BUTTON = 4
    GROUP = 5

class SaneScannerOption(BaseModel):
    index: NonNegativeInt
    name: str|None
    title: str
    desc: str|None
    type: NonNegativeInt
    unit: NonNegativeInt
    size: NonNegativeInt
    cap: NonNegativeInt
    constraint: None|tuple[NonNegativeInt, NonNegativeInt, NonNegativeInt]|list[int]|list[float]|list[str]


class SaneScanner(BaseModel):
    device_name: str
    vendor: str
    model: str
    type_info: str
    options: list[SaneScannerOption]

class ScanOptions(BaseModel):
    scanner: str
    resolution: PositiveInt
    source: str
    mode: str
    filename: str = Field(default="")