from simple_webscan.ScannerModels import SaneScanner, ScanOptions
from pathlib import Path
import threading

globals_lock = threading.Lock()
sane_lock = threading.Lock()
scanners: dict[str, SaneScanner] = {}
isBusy: set[str] = set()
scan_list_update = False
has_front = set()
last_scan_options: dict[str, ScanOptions] = {}
last_scan_filenames: dict[str, Path] = {}
