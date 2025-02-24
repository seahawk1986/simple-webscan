import logging
from contextlib import closing
from functools import wraps
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pymupdf
import sane
from PIL import Image

from simple_webscan.config import load_config
from simple_webscan.ScannerModels import SaneScanner, SaneScannerOption, ScanOptions
from simple_webscan import state

config = load_config()

def using_sane(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        logging.debug("initializing sane")
        sane.init()
        try:
            return f(*args, **kwargs)
        finally:
            logging.debug("disconnecting sane")
            sane.exit()

    return wrapper




def busy_device(devicename: str):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            state.isBusy.add(devicename)
            try:
                return f(*args, **kwargs)
            finally:
                print(f"releasing {devicename}")
                state.isBusy.remove(devicename)

        return wrapper

    return decorator

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

def update_scanners():
    @using_sane
    def do_list_scanners():
        return list_scanners()

    logging.info("searching for scanners...")
    with state.sane_lock:
        r = do_list_scanners()
    with state.globals_lock:
        state.scanners = r
    logging.debug("scanner list updated")


def update_scanlist():
    with state.globals_lock:
        state.scan_list_update = True

    update_scanners()

    with state.globals_lock:
        state.scan_list_update = False


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

def perform_scan(data: state.ScanOptions):
    @using_sane
    @busy_device(data.scanner)
    def do_scan(data):
        return scan(data)

    with state.sane_lock:
        target = do_scan(data)
    if target:
        logging.info("scan completed")
        with state.globals_lock:
            state.last_scan_options[data.scanner] = data
            state.last_scan_filenames[data.scanner] = target
            state.has_front.add(data.scanner)
        return target

def add_backside(data: state.ScanOptions):
    config = load_config()
    if not (frontside_file := state.last_scan_filenames.get(data.scanner)):
        raise FileExistsError("no front side defined")
    data.filename = f"{frontside_file.name}_backside.pdf"
    if frontside_file and (backside_file := perform_scan(data)):
        frontside_file = config.scandir / frontside_file
        backside_file = config.scandir / backside_file
        with closing(result := pymupdf.open()), closing(
            front := pymupdf.open(frontside_file)
        ), closing(back := pymupdf.open(backside_file)):
            if front.page_count != back.page_count:
                logging.error("page numbers don't match, skipping")
            else:
                for (n, front_page), (o, back_page) in zip(
                    enumerate(front.pages()), reversed(list(enumerate(back.pages())))
                ):
                    result.insert_pdf(front, from_page=n, to_page=n)
                    result.insert_pdf(back, from_page=o, to_page=o)
                result.save(frontside_file)
        backside_file.unlink()
        with state.globals_lock:
            state.has_front.discard(data.scanner)
            state.last_scan_filenames.pop(data.scanner)