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
                    options.append(
                        SaneScannerOption(
                            index=idx,
                            name=name,
                            title=title,
                            desc=desc,
                            type=type_,
                            unit=unit,
                            size=size,
                            cap=cap,
                            constraint=constraint,
                        )
                    )

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
    print(f"processing page {n}")
    path = tmpdir / f"scan_{n}.jpg"

    # Bild auf SSD/HDD speichern, um RAM sofort freizugeben
    scan.save(path, optimize=True, subsampling=0, quality=95)

    # Neue leere Seite in den Abmessungen des Scans erstellen
    # (Pillow nutzt .width/.height in Pixeln; bei Scans entspricht das meist den PDF-Punkten)
    page = output.new_page(width=scan.width, height=scan.height)

    # Bild direkt auf die Seite zeichnen (erzeugt weniger Overhead als convert_to_pdf)
    page.insert_image(page.rect, filename=str(path))


def scan(options: ScanOptions) -> Path | None:
    print(f"scan with {options.scanner}")

    target = Path(
        options.filename
        if options.filename
        else f"Scan_{datetime.now():%Y-%m-%d_%H_%M_%S}.pdf"
    ).resolve()
    target = Path(target.name)
    if not target.suffix == ".pdf":
        target = target.with_suffix(target.suffix + ".pdf")

    final_path = config.scandir / target

    with TemporaryDirectory() as tmp, sane.SaneDev(options.scanner) as scanner:
        print(f"scanner {options.scanner} opened")
        tmpdir = Path(tmp)
        scanner.source = options.source
        scanner.resolution = options.resolution
        scanner.mode = options.mode

        if options.source == "ADF":
            scans_iterator = enumerate(scanner.multi_scan())

            # get the first page
            try:
                n, first_scan = next(scans_iterator)
            except StopIteration:
                print("no pages were scanned")
                return None

            # write it to disk
            output = pymupdf.open()
            process_page(tmpdir, output, first_scan, n)
            output.save(final_path)
            output.close()  # now we have a one page document, that mupdf can save

            # resuse file to write the other pages incrementally
            with pymupdf.open(final_path) as output:
                for n, scan_img in scans_iterator:
                    process_page(tmpdir, output, scan_img, n)
                    # write the updated page to disk
                    output.save(
                        output.name,
                        incremental=True,
                        encryption=pymupdf.PDF_ENCRYPT_KEEP,
                    )

            has_pages = True

        else:
            scan_img = scanner.scan()
            output = pymupdf.open()
            process_page(tmpdir, output, scan_img)
            output.save(final_path)
            output.close()
            has_pages = True

        if has_pages:
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

        with pymupdf.open(frontside_file) as front, pymupdf.open(backside_file) as back:
            if front.page_count != back.page_count:
                logging.error("page numbers don't match, skipping")
            else:
                back_pages_reversed = list(reversed(range(back.page_count)))

                for step, back_idx in enumerate(back_pages_reversed):
                    insert_pos = (step * 2) + 1

                    front.insert_pdf(
                        back, from_page=back_idx, to_page=back_idx, start_at=insert_pos
                    )

                    # Sofort auf die Festplatte schreiben und RAM leeren!
                    front.save(
                        front.name,
                        incremental=True,
                        encryption=pymupdf.PDF_ENCRYPT_KEEP,
                    )

        backside_file.unlink()

        with state.globals_lock:
            state.has_front.discard(data.scanner)
            state.last_scan_filenames.pop(data.scanner)
