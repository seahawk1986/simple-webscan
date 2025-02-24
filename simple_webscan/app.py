from contextlib import asynccontextmanager
import logging
from pathlib import Path
from urllib.parse import quote


from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from simple_webscan.config import load_config
from simple_webscan import state, scanner
from simple_webscan.ScannerModels import SaneType

module_dir = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Read the scanners on startup - this takes some time,
    # the application won't respond until the scan is done
    scanner.update_scanners()
    yield

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=module_dir / "static"), name="static")
load_config().scandir.mkdir(exist_ok=True, parents=True)


templates = Jinja2Templates(directory=module_dir / "templates")
templates.env.filters["uriquote"] = lambda x: quote(str(x)) if x else ""


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    for scanner_name, scanner_device in state.scanners.items():
        logging.info(f"{scanner_name}: {scanner_device}")
    with state.globals_lock:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "scanners": state.scanners,
                "scan_list_update": state.scan_list_update,
            },
        )


@app.get("/list_scanners", response_class=HTMLResponse)
def get_scanners(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(scanner.update_scanlist)
    return templates.TemplateResponse(
        request=request,
        name="updating_scanners.html",
        context={"scanners": state.scanners},
    )


@app.get("/scanner_update_status", response_class=HTMLResponse)
def get_scanlist_update_status(request: Request):
    if state.scan_list_update:
        return templates.TemplateResponse(
            request=request, name="updating_scanners.html"
        )
    else:
        return templates.TemplateResponse(
            request=request,
            name="scanner_home.html",
            context={"scanners": state.scanners},
        )

@app.get("/isBusy/{scanner_name:path}", response_class=HTMLResponse)
async def is_busy(request: Request, scanner_name: str):
    global isBusy
    global has_front
    print(f"busy scanners: {state.isBusy}")
    with state.globals_lock:
        sane_scanner = state.scanners[scanner_name]
    if scanner_name in state.isBusy:
        return templates.TemplateResponse(
            request=request, name="scanning.html", context={"scanner": sane_scanner}
        )
    elif sane_scanner.device_name in state.has_front:
        return templates.TemplateResponse(
            request=request, name="scan_backside.html", context={"scanner": sane_scanner}
        )
    else:
        return templates.TemplateResponse(
            request=request,
            name="scanoptions.html",
            context={"scanner": sane_scanner, "SANE_TYPE": SaneType, "config": load_config()},
        )


@app.get("/scanner/{scanner_name:path}", response_class=HTMLResponse)
def get_scan_site(request: Request, scanner_name: str):
    sane_scanner = state.scanners[scanner_name]
    config = load_config()
    return templates.TemplateResponse(
        request=request,
        name="scanpage.html",
        context={
            "scanner": sane_scanner,
            "SANE_TYPE": SaneType,
            "has_front": sane_scanner.device_name in state.has_front,
            "isBusy": state.isBusy,
            "config": config,
        },
    )


@app.post("/scan", response_class=HTMLResponse)
async def start_scan(
    request: Request, data: state.ScanOptions, background_tasks: BackgroundTasks
):
    # global isBusy
    logging.debug(data)
    background_tasks.add_task(scanner.perform_scan, data)
    sane_scanner = state.scanners[data.scanner]
    return templates.TemplateResponse(
        request=request,
        name="scanning.html",
        context={
            "scanner": sane_scanner,
            "SANE_TYPE": SaneType,
            "has_front": sane_scanner.device_name in state.has_front,
            "isBusy": state.isBusy,
        },
    )


@app.post("/scan_backside/{scanner_name:path}", response_class=HTMLResponse)
async def backside_scan(
    request: Request, scanner_name: str, background_tasks: BackgroundTasks
):
    with state.globals_lock:
        sane_scanner = state.scanners[scanner_name]
        background_tasks.add_task(scanner.add_backside, state.last_scan_options[sane_scanner.device_name])
        return templates.TemplateResponse(
            request=request,
            name="scanning.html",
            context={
                "scanner": sane_scanner,
            },
        )


@app.post("/done/{scanner_name:path}", response_class=HTMLResponse)
async def reset_front(request: Request, scanner_name: str):
    global has_front
    config = load_config()
    with state.globals_lock:
        sane_scanner = state.scanners[scanner_name]
        state.has_front.discard(sane_scanner.device_name)
    return templates.TemplateResponse(
        request=request,
        name="scanoptions.html",
        context={"scanner": sane_scanner, "SANE_TYPE": SaneType, 'config': config},
    )
