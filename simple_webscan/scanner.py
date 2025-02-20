import logging
from contextlib import closing
from enum import IntEnum
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from pydantic import BaseModel, Field, NonNegativeInt, PositiveInt

import pymupdf
import sane
from PIL import Image

from simple_webscan.config import load_config

config = load_config()

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

scanners: dict[str, SaneScanner] = {}
isBusy: set[str] = set()

def list_scanners() -> dict[str, SaneScanner]:
    devices = {}
    for device in sane.get_devices():
        device_name, vendor, model, type_info = device
        options = []
        try:
            with sane.SaneDev(device_name) as scanner:
                for o in scanner.get_options():
                    idx, name, title, desc, type_, unit, size, cap, constraint = o
                    options.append(SaneScannerOption(
                        index=idx,
                        name=name,
                        title=title,
                        desc=desc,
                        type=type_,
                        unit=unit,
                        size=size,
                        cap=cap,
                        constraint=constraint,
                    ))

            devices[device_name] = SaneScanner(
                device_name=device_name,
                vendor=vendor,
                model=model,
                type_info=type_info,
                options=options,
            )
        except Exception as e:
            logging.error(e)
    return devices


def process_page(tmpdir: Path, output: pymupdf.Document, scan: Image.Image, n=0):
    print(f'scanned page {n}')
    path = tmpdir / f'scan_{n}.jpg'
    scan.save(path, optimize=True, subsampling=0, quality=95)
    with closing(img := pymupdf.open(path)):
        rect = img[0].rect
        pdfbytes = img.convert_to_pdf()
    imgPDF = pymupdf.open('pdf', pdfbytes)
    page = output.new_page(width = rect.width, height = rect.height) # type: ignore
    page.show_pdf_page(rect, imgPDF, 0)


def scan(options: ScanOptions) -> Path|None:
    print(f"scan with {options.scanner}")
    with TemporaryDirectory() as tmp, closing(output := pymupdf.open()), sane.SaneDev(options.scanner) as scanner:
        print(f"scanner {options.scanner} opened")
        tmpdir = Path(tmp)
        scanner.source = options.source
        scanner.resolution = options.resolution
        scanner.mode = options.mode
        target = Path(options.filename if options.filename else f'Scan_{datetime.now():%Y-%m-%d_%H_%M_%S}.pdf').resolve()
        target = Path(target.name)
        if not target.suffix == '.pdf':
            target = target.with_suffix(target.suffix + '.pdf')

        scan: Image.Image
        if options.source == 'ADF':
            for n, scan in enumerate(scanner.multi_scan()):
                process_page(tmpdir, output, scan, n)
        else:
            scan = scanner.scan()
            process_page(tmpdir, output, scan)
            
        if output.page_count:
            output.save(config.scandir / target)
            return Path(target)
        else:
            print("no pages were scanned")
            return None
