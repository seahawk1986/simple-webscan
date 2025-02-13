from contextlib import asynccontextmanager, closing
import logging
from pathlib import Path
import threading
from functools import wraps
from multiprocessing import Pool
from urllib.parse import quote

import pymupdf
import sane
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import config
from scanner import (
    ScanOptions,
    list_scanners,
    scan,
    scanners as global_scanner_dict,
    isBusy,
    SaneType,
)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
globals_lock = threading.Lock()
sane_lock = threading.Lock()

templates = Jinja2Templates(directory="templates")
templates.env.filters["uriquote"] = lambda x: quote(str(x)) if x else ""

scan_list_update = False
has_front = set()
last_scan_options: dict[str, ScanOptions] = {}
last_scan_filenames: dict[str, Path] = {}


def using_sane(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        sane.init()
        try:
            return f(*args, **kwargs)
        finally:
            print("disconnecting sane")
            sane.exit()

    return wrapper


def busy_device(devicename: str):
    global isBusy

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            isBusy.add(devicename)
            try:
                return f(*args, **kwargs)
            finally:
                print(f"releasing {devicename}")
                isBusy.remove(devicename)

        return wrapper

    return decorator


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    for scanner_name, scanner in global_scanner_dict.items():
        logging.info(f"{scanner_name}: {scanner}")
    with globals_lock:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "scanners": global_scanner_dict,
                "scan_list_update": scan_list_update,
            },
        )


@app.get("/list_scanners", response_class=HTMLResponse)
def get_scanners(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(update_scanlist)
    return templates.TemplateResponse(
        request=request,
        name="updating_scanners.html",
        context={"scanners": global_scanner_dict},
    )


@app.get("/scanner_update_status", response_class=HTMLResponse)
def get_scanlist_update_status(request: Request):
    if scan_list_update:
        return templates.TemplateResponse(
            request=request, name="updating_scanners.html"
        )
    else:
        return templates.TemplateResponse(
            request=request,
            name="scanner_home.html",
            context={"scanners": global_scanner_dict},
        )


def update_scanlist():
    global scan_list_update
    with globals_lock:
        scan_list_update = True

    update_scanners()

    with globals_lock:
        scan_list_update = False


def update_scanners():
    global global_scanner_dict

    @using_sane
    def do_list_scanners():
        return list_scanners()

    logging.info("searching for scanners...")
    with sane_lock:
        r = do_list_scanners()
    with globals_lock:
        global_scanner_dict = r
    logging.debug("scanner list updated")


@app.get("/isBusy/{scanner_name:path}", response_class=HTMLResponse)
async def is_busy(request: Request, scanner_name: str):
    global isBusy
    global has_front
    print(f"busy scanners: {isBusy}")
    with globals_lock:
        scanner = global_scanner_dict[scanner_name]
    if scanner_name in isBusy:
        return templates.TemplateResponse(
            request=request, name="scanning.html", context={"scanner": scanner}
        )
    elif scanner.device_name in has_front:
        return templates.TemplateResponse(
            request=request, name="scan_backside.html", context={"scanner": scanner}
        )
    else:
        return templates.TemplateResponse(
            request=request,
            name="scanoptions.html",
            context={"scanner": scanner, "SANE_TYPE": SaneType, "config": config},
        )


@app.get("/scanner/{scanner_name:path}", response_class=HTMLResponse)
def get_scan_site(request: Request, scanner_name: str):
    scanner = global_scanner_dict[scanner_name]
    return templates.TemplateResponse(
        request=request,
        name="scanpage.html",
        context={
            "scanner": scanner,
            "SANE_TYPE": SaneType,
            "has_front": scanner.device_name in has_front,
            "isBusy": isBusy,
            "config": config,
        },
    )


def perform_scan(data: ScanOptions):
    global has_front, last_scan_options

    @using_sane
    @busy_device(data.scanner)
    def do_scan(data):
        return scan(data)

    with sane_lock:
        target = do_scan(data)
    if target:
        logging.info("scan completed")
        with globals_lock:
            last_scan_options[data.scanner] = data
            last_scan_filenames[data.scanner] = target
            has_front.add(data.scanner)
        return target

def add_backside(data: ScanOptions):
    global has_front, last_scan_filenames
    if not (frontside_file := last_scan_filenames.get(data.scanner)):
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
        with globals_lock:
            has_front.discard(data.scanner)
            last_scan_filenames.pop(data.scanner)


@app.post("/scan", response_class=HTMLResponse)
async def start_scan(
    request: Request, data: ScanOptions, background_tasks: BackgroundTasks
):
    # global isBusy
    logging.debug(data)
    background_tasks.add_task(perform_scan, data)
    scanner = global_scanner_dict[data.scanner]
    return templates.TemplateResponse(
        request=request,
        name="scanning.html",
        context={
            "scanner": scanner,
            "SANE_TYPE": SaneType,
            "has_front": scanner.device_name in has_front,
            "isBusy": isBusy,
        },
    )


@app.post("/scan_backside/{scanner_name:path}", response_class=HTMLResponse)
async def backside_scan(
    request: Request, scanner_name: str, background_tasks: BackgroundTasks
):
    with globals_lock:
        scanner = global_scanner_dict[scanner_name]
        background_tasks.add_task(add_backside, last_scan_options[scanner.device_name])
        return templates.TemplateResponse(
            request=request,
            name="scanning.html",
            context={
                "scanner": scanner,
            },
        )


@app.post("/done/{scanner_name:path}", response_class=HTMLResponse)
async def reset_front(request: Request, scanner_name: str):
    global has_front
    with globals_lock:
        scanner = global_scanner_dict[scanner_name]
        has_front.discard(scanner.device_name)
    return templates.TemplateResponse(
        request=request,
        name="scanoptions.html",
        context={"scanner": scanner, "SANE_TYPE": SaneType, 'config': config},
    )


# Read the scanners on startup - this takes some time,
# the application won't respond until the scan is done
update_scanners()
